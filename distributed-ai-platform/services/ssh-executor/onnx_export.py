"""Export the platform's pure-Python models as portable ONNX graphs."""
from __future__ import annotations

import json

import onnx
from onnx import TensorProto, helper


def _build_cnn_onnx(artifact: dict) -> bytes:
    """Build the baseline-CNN graph from the trained flat parameter vector.

    Mirrors ray_templates / ml_templates._evaluate_cnn exactly:
    Conv(valid 3x3, 1->F) -> Relu -> MaxPool(2x2 s2) -> Flatten -> MatMul(fc_w
    [flat,C]) -> Add(fc_b) -> Softmax. Flat layout
    ``[conv_w(F*9), conv_b(F), fc_w(flat*C), fc_b(C)]``.
    """
    params = artifact.get("w") or []
    arch = artifact.get("arch") or {}
    f = int(arch.get("F", 8))
    side = int(arch.get("side", 0))
    c = int(arch.get("C", 0))
    if not params or side <= 0 or c <= 0:
        raise ValueError("CNN artifact missing weights or arch (F/side/C)")
    oh = side - 2
    ph = oh // 2
    flat = f * ph * ph
    fc_base = f * 9 + f
    expected = f * 9 + f + flat * c + c
    if len(params) != expected:
        raise ValueError(f"CNN weight vector length {len(params)} != expected {expected}")

    conv_w = [float(v) for v in params[0:f * 9]]
    conv_b = [float(v) for v in params[f * 9:f * 9 + f]]
    fc_w = [float(v) for v in params[fc_base:fc_base + flat * c]]
    fc_b = [float(v) for v in params[fc_base + flat * c:]]

    images = helper.make_tensor_value_info("images", TensorProto.FLOAT, [None, 1, side, side])
    probs = helper.make_tensor_value_info("probabilities", TensorProto.FLOAT, [None, c])
    inits = [
        helper.make_tensor("conv_w", TensorProto.FLOAT, [f, 1, 3, 3], conv_w),
        helper.make_tensor("conv_b", TensorProto.FLOAT, [f], conv_b),
        helper.make_tensor("fc_w", TensorProto.FLOAT, [flat, c], fc_w),
        helper.make_tensor("fc_b", TensorProto.FLOAT, [c], fc_b),
    ]
    nodes = [
        helper.make_node("Conv", ["images", "conv_w", "conv_b"], ["conv"],
                         kernel_shape=[3, 3], pads=[0, 0, 0, 0], strides=[1, 1]),
        helper.make_node("Relu", ["conv"], ["relu"]),
        helper.make_node("MaxPool", ["relu"], ["pool"], kernel_shape=[2, 2], strides=[2, 2]),
        helper.make_node("Flatten", ["pool"], ["flat"], axis=1),
        helper.make_node("MatMul", ["flat", "fc_w"], ["mm"]),
        helper.make_node("Add", ["mm", "fc_b"], ["logits"]),
        helper.make_node("Softmax", ["logits"], ["probabilities"], axis=1),
    ]
    graph = helper.make_graph(nodes, "cnn_model", [images], [probs], initializer=inits)
    model = helper.make_model(
        graph, producer_name="distributed-ai-platform",
        opset_imports=[helper.make_opsetid("", 13)],
    )
    entry = model.metadata_props.add()
    entry.key, entry.value = "model_type", "cnn"
    entry2 = model.metadata_props.add()
    entry2.key, entry2.value = "output_semantics", "class_probabilities"
    onnx.checker.check_model(model)
    return model.SerializeToString()


def build_onnx_model(model_type: str, weights_json: str) -> bytes:
    """Build an ONNX model matching the trained platform model exactly."""
    if model_type not in {"linear_regression", "logistic_regression", "cnn"}:
        raise ValueError(f"Unsupported model_type: {model_type}")

    artifact = json.loads(weights_json)
    if model_type == "cnn":
        return _build_cnn_onnx(artifact)

    weights = artifact.get("w")
    if not isinstance(weights, list) or not weights:
        raise ValueError("Training weights are missing")
    bias = float(artifact.get("b", 0.0))
    feature_names = artifact.get("feature_names", [])
    feature_count = len(weights)

    input_info = helper.make_tensor_value_info(
        "features", TensorProto.FLOAT, [None, feature_count]
    )
    output_info = helper.make_tensor_value_info(
        "prediction", TensorProto.FLOAT, [None, 1]
    )
    weight_tensor = helper.make_tensor(
        "weights",
        TensorProto.FLOAT,
        [feature_count, 1],
        [float(value) for value in weights],
    )
    bias_tensor = helper.make_tensor("bias", TensorProto.FLOAT, [1], [bias])

    nodes = [
        helper.make_node("MatMul", ["features", "weights"], ["linear_score"]),
        helper.make_node("Add", ["linear_score", "bias"], ["score"]),
    ]
    if model_type == "logistic_regression":
        nodes.append(helper.make_node("Sigmoid", ["score"], ["prediction"]))
    else:
        nodes.append(helper.make_node("Identity", ["score"], ["prediction"]))

    graph = helper.make_graph(
        nodes,
        f"{model_type}_model",
        [input_info],
        [output_info],
        initializer=[weight_tensor, bias_tensor],
    )
    model = helper.make_model(
        graph,
        producer_name="distributed-ai-platform",
        opset_imports=[helper.make_opsetid("", 13)],
    )
    metadata = {
        "model_type": model_type,
        "feature_names": json.dumps(feature_names),
        "output_semantics": "sigmoid_probability" if model_type == "logistic_regression" else "regression_value",
    }
    for key, value in metadata.items():
        entry = model.metadata_props.add()
        entry.key = key
        entry.value = value
    onnx.checker.check_model(model)
    return model.SerializeToString()