import { useState } from 'react';
import { ClusterConfigForm } from '../components/cluster/ClusterConfigForm';
import { DataUpload, QuickSplitConfig } from '../components/data/DataUpload';
import { ModelConfig } from '../components/train/ModelConfig';
import { PipelineProgress } from '../components/progress/PipelineProgress';
import { ResultsPanel } from '../components/results/ResultsPanel';
import { PrimaryButton, SecondaryButton, Card } from '../components/common/Primitives';
import { useClusterConfig } from '../context/ClusterConfigContext';
import { useToast } from '../context/ToastContext';
import { submitPreprocessingJob } from '../api/preprocessing';
import { submitTrainingJob } from '../api/training';
import type { Dataset, HyperparameterGrid, ProblemType, PreprocessingConfig, TrainingConfig } from '../types';

type WizardStep = 'configure' | 'progress' | 'results';

const DEFAULT_QUICK_SPLIT: QuickSplitConfig = {
  numericFeatures: [],
  categoricalFeatures: [],
  targetColumn: '',
  trainRatio: 0.7,
  validationRatio: 0.15,
  testRatio: 0.15,
};

export function TrainPage() {
  const { config: clusterConfig } = useClusterConfig();
  const { showError } = useToast();

  const [step, setStep] = useState<WizardStep>('configure');

  // data selection
  const [dataset, setDataset] = useState<Dataset | null>(null);
  const [quickSplit, setQuickSplit] = useState<QuickSplitConfig>(DEFAULT_QUICK_SPLIT);

  // model selection
  const [problemType, setProblemType] = useState<ProblemType>('classification');
  const [algorithms, setAlgorithms] = useState<HyperparameterGrid[]>([]);
  const [cvFolds, setCvFolds] = useState(3);
  const [primaryMetric, setPrimaryMetric] = useState('f1');

  // job tracking
  const [preprocessingJobId, setPreprocessingJobId] = useState<string | null>(null);
  const [trainingJobId, setTrainingJobId] = useState<string | null>(null);
  const [mlflowRunId, setMlflowRunId] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const ratioSum = quickSplit.trainRatio + quickSplit.validationRatio + quickSplit.testRatio;
  const canSubmit =
    !!dataset &&
    quickSplit.numericFeatures.length > 0 &&
    (problemType === 'clustering' || !!quickSplit.targetColumn) &&
    Math.abs(ratioSum - 1) < 0.01 &&
    algorithms.length > 0;

  const handleStartTraining = async () => {
    if (!dataset) return;
    setSubmitting(true);
    try {
      const preprocessingConfig: PreprocessingConfig = {
        numeric_features: quickSplit.numericFeatures,
        categorical_features: quickSplit.categoricalFeatures,
        target_column: problemType === 'clustering' ? null : quickSplit.targetColumn,
        drop_columns: [],
        null_strategy: 'drop', // data is already preprocessed; kept minimal/safe default
        scaler_type: 'standard',
        train_ratio: quickSplit.trainRatio,
        validation_ratio: quickSplit.validationRatio,
        test_ratio: quickSplit.testRatio,
        random_seed: 42,
      };

      const preJob = await submitPreprocessingJob(dataset.id, preprocessingConfig, clusterConfig);
      setPreprocessingJobId(preJob.job_id);
      setStep('progress');

      // NOTE: this fires the training submission immediately rather than
      // waiting for preprocessing to actually finish, because the backend
      // doesn't expose a webhook/callback the frontend can await -- see
      // PipelineProgress.tsx's note on job-status polling gaps. In a
      // finished backend, training submission should instead be triggered
      // automatically once preprocessing's status flips to "succeeded".
      const trainingConfig: TrainingConfig = {
        problem_type: problemType,
        algorithms,
        cv_folds: cvFolds,
        primary_metric: primaryMetric,
        higher_is_better: primaryMetric !== 'rmse',
        parallelism: 3,
      };
      const trainJob = await submitTrainingJob(preJob.job_id, trainingConfig, clusterConfig);
      setTrainingJobId(trainJob.job_id);
    } catch (e: any) {
      showError(e.message ?? 'Failed to start the pipeline');
      setStep('configure');
    } finally {
      setSubmitting(false);
    }
  };

  if (step === 'results' && mlflowRunId && dataset) {
    return <ResultsPanel mlflowRunId={mlflowRunId} dataset={dataset} problemType={problemType} />;
  }

  if (step === 'progress' && preprocessingJobId) {
    return (
      <PipelineProgress
        preprocessingJobId={preprocessingJobId}
        trainingJobId={trainingJobId}
        onTrainingReady={(runId) => {
          setMlflowRunId(runId);
          setStep('results');
        }}
      />
    );
  }

  return (
    <div className="w-full max-w-4xl mx-auto space-y-6">
      <ClusterConfigForm />

      <DataUpload
        selectedDataset={dataset}
        onSelectDataset={setDataset}
        quickSplit={quickSplit}
        onQuickSplitChange={setQuickSplit}
      />

      <ModelConfig
        problemType={problemType}
        onProblemTypeChange={setProblemType}
        selected={algorithms}
        onSelectedChange={setAlgorithms}
        cvFolds={cvFolds}
        onCvFoldsChange={setCvFolds}
        primaryMetric={primaryMetric}
        onPrimaryMetricChange={setPrimaryMetric}
      />

      <Card>
        <div className="flex items-center justify-between">
          <div className="text-sm text-slate-600 font-medium">
            {!dataset && 'Select a dataset to continue.'}

            {dataset &&
              Math.abs(ratioSum - 1) >= 0.01 &&
              'Split ratios must sum to 1.0.'}

            {dataset &&
              Math.abs(ratioSum - 1) < 0.01 &&
              algorithms.length === 0 &&
              'Select at least one algorithm.'}

            {canSubmit && (
              <span className="text-emerald-600 font-semibold">
                Ready to train.
              </span>
            )}
          </div>

          <PrimaryButton
            onClick={handleStartTraining}
            disabled={!canSubmit || submitting}
          >
            {submitting ? 'Starting…' : 'Start Training'}
          </PrimaryButton>
        </div>
      </Card>
    </div>
  );
}