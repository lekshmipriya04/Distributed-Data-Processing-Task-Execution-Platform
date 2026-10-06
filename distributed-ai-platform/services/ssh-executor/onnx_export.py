"""Export the platform's pure-Python models as portable ONNX graphs."""
from __future__ import annotations

import json

import onnx
from onnx import TensorProto, helper


def build_onnx_model(model_type: str, weights_json: str) -> bytes:
    """Build an ONNX model matching the trained platform model exactly."""
    if model_type not in {"linear_regression", "logistic_regression"}:
        raise ValueError(f"Unsupported model_type: {model_type}")

    artifact = json.loads(weights_json)
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