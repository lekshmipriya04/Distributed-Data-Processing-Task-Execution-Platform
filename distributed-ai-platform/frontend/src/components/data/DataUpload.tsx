import { useEffect, useRef, useState } from 'react';
import { Database, FileSpreadsheet, Image as ImageIcon, RefreshCw, Trash2, Upload, Loader2, CheckCircle2 } from 'lucide-react';
import { Card, SectionTitle, PrimaryButton, NumberField, TagInput, ConfirmDialog } from '../common/Primitives';
import { useToast } from '../../context/ToastContext';
import { errorMessage } from '../../api/client';
import {
  deleteDataset,
  listDatasets,
  parseDatasetSchema,
  readCsvHeader,
  uploadDataset,
  uploadImageDataset,
} from '../../api/storage';
import type { Dataset } from '../../types';

/** Column choice for a training run; maps 1:1 onto TrainRequest.target_column / feature_columns. */
export interface QuickSplitConfig {
  targetColumn: string;
  featureColumns: string[];
}

type UploadKind = 'table' | 'images';

// Header columns read client-side from CSV uploads, keyed by dataset id.
// storage-service does not record columns for tabular uploads, so this lets
// the pickers offer real column names. Per-browser convenience only.
const COLUMNS_KEY = 'platform_dataset_columns';

function loadColumnCache(): Record<string, string[]> {
  try {
    const raw = localStorage.getItem(COLUMNS_KEY);
    const parsed = raw ? JSON.parse(raw) : {};
    return parsed && typeof parsed === 'object' ? parsed : {};
  } catch {
    return {};
  }
}

function saveColumnCache(cache: Record<string, string[]>) {
  try {
    localStorage.setItem(COLUMNS_KEY, JSON.stringify(cache));
  } catch {
    /* storage unavailable */
  }
}

const INPUT_CLS =
  'w-full bg-white rounded-lg px-3 py-2 border border-slate-300 focus:border-blue-500 focus:ring-1 focus:ring-blue-500 outline-hidden text-sm';

export function DataUpload({
  selectedDataset,
  onSelectDataset,
  quickSplit,
  onQuickSplitChange,
}: {
  selectedDataset: Dataset | null;
  onSelectDataset: (dataset: Dataset | null) => void;
  quickSplit: QuickSplitConfig;
  onQuickSplitChange: (config: QuickSplitConfig) => void;
}) {
  const { showError, showSuccess } = useToast();

  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [loading, setLoading] = useState(false);
  const [columnCache, setColumnCache] = useState<Record<string, string[]>>(loadColumnCache);

  const [kind, setKind] = useState<UploadKind>('table');
  const [file, setFile] = useState<File | null>(null);
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [imageSize, setImageSize] = useState(8);
  const [uploading, setUploading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const [pendingDelete, setPendingDelete] = useState<Dataset | null>(null);

  const refresh = async () => {
    setLoading(true);
    try {
      const res = await listDatasets();
      setDatasets(res.items);
    } catch (e) {
      showError(errorMessage(e, 'Failed to load datasets'));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const columnsFor = (dataset: Dataset | null, cache = columnCache): string[] => {
    if (!dataset) return [];
    const schema = parseDatasetSchema(dataset);
    if (schema.columns?.length) return schema.columns;
    return cache[dataset.id] ?? [];
  };

  const defaultsFor = (dataset: Dataset, cache = columnCache): QuickSplitConfig => {
    const schema = parseDatasetSchema(dataset);
    if (schema.target_column && schema.feature_columns?.length) {
      return { targetColumn: schema.target_column, featureColumns: schema.feature_columns };
    }
    const cols = columnsFor(dataset, cache);
    if (cols.length >= 2) {
      const target = cols[cols.length - 1];
      return { targetColumn: target, featureColumns: cols.slice(0, -1) };
    }
    return { targetColumn: '', featureColumns: [] };
  };

  const select = (dataset: Dataset, cache = columnCache) => {
    onSelectDataset(dataset);
    onQuickSplitChange(defaultsFor(dataset, cache));
  };

  const onPickFile = (f: File | null) => {
    setFile(f);
    if (f && !name) setName(f.name.replace(/\.[^.]+$/, ''));
  };

  const resetForm = () => {
    setFile(null);
    setName('');
    setDescription('');
    if (fileInputRef.current) fileInputRef.current.value = '';
  };

  const handleUpload = async () => {
    if (!file || !name.trim()) return;
    setUploading(true);
    try {
      let created: Dataset;
      let cache = columnCache;
      if (kind === 'images') {
        created = await uploadImageDataset(file, name.trim(), {
          description: description.trim() || undefined,
          imageSize,
          color: 'grayscale',
        });
      } else {
        const header = await readCsvHeader(file);
        created = await uploadDataset(file, name.trim(), description.trim() || undefined);
        if (header.length) {
          cache = { ...columnCache, [created.id]: header };
          setColumnCache(cache);
          saveColumnCache(cache);
        }
      }
      showSuccess(`Dataset "${created.name}" uploaded`);
      setDatasets((cur) => [created, ...cur.filter((d) => d.id !== created.id)]);
      select(created, cache);
      resetForm();
    } catch (e) {
      showError(errorMessage(e, 'Upload failed'));
    } finally {
      setUploading(false);
    }
  };

  const confirmDelete = async () => {
    const target = pendingDelete;
    setPendingDelete(null);
    if (!target) return;
    try {
      await deleteDataset(target.id);
      setDatasets((cur) => cur.filter((d) => d.id !== target.id));
      if (columnCache[target.id]) {
        const { [target.id]: _removed, ...rest } = columnCache;
        setColumnCache(rest);
        saveColumnCache(rest);
      }
      if (selectedDataset?.id === target.id) {
        onSelectDataset(null);
        onQuickSplitChange({ targetColumn: '', featureColumns: [] });
      }
      showSuccess(`Dataset "${target.name}" deleted`);
    } catch (e) {
      showError(errorMessage(e, 'Delete failed'));
    }
  };

  const selectedSchema = parseDatasetSchema(selectedDataset);
  const isImage = Boolean(selectedSchema.image);
  const columns = columnsFor(selectedDataset);

  const setTarget = (target: string) =>
    onQuickSplitChange({
      targetColumn: target,
      featureColumns: quickSplit.featureColumns.filter((c) => c !== target),
    });

  const toggleFeature = (col: string) =>
    onQuickSplitChange({
      ...quickSplit,
      featureColumns: quickSplit.featureColumns.includes(col)
        ? quickSplit.featureColumns.filter((c) => c !== col)
        : [...quickSplit.featureColumns, col],
    });

  return (
    <Card>
      <SectionTitle>Dataset</SectionTitle>

      {/* Upload ------------------------------------------------------------ */}
      <div className="flex gap-2 mb-4">
        {(
          [
            { value: 'table', label: 'Table (CSV / Parquet)', icon: <FileSpreadsheet size={16} /> },
            { value: 'images', label: 'Images (ZIP)', icon: <ImageIcon size={16} /> },
          ] as const
        ).map((opt) => (
          <button
            key={opt.value}
            type="button"
            onClick={() => {
              setKind(opt.value);
              resetForm();
            }}
            className={`flex items-center gap-2 px-3.5 py-2 rounded-lg border text-sm font-medium transition-all ${
              kind === opt.value
                ? 'bg-blue-50 border-blue-500 text-blue-700'
                : 'bg-white border-slate-200 text-slate-600 hover:border-slate-300'
            }`}
          >
            {opt.icon} {opt.label}
          </button>
        ))}
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <label className="text-sm sm:col-span-2">
          <span className="text-xs text-slate-500 font-medium block mb-1">
            {kind === 'images'
              ? 'ZIP laid out as <label>/<image files> (folder name = class)'
              : 'CSV or Parquet file with a header row'}
          </span>
          <input
            ref={fileInputRef}
            type="file"
            accept={kind === 'images' ? '.zip,application/zip' : '.csv,.parquet,text/csv'}
            onChange={(e) => onPickFile(e.target.files?.[0] ?? null)}
            className="block w-full text-sm text-slate-600 file:mr-3 file:px-3 file:py-1.5 file:rounded-lg file:border-0 file:bg-slate-100 file:text-slate-700 file:font-medium hover:file:bg-slate-200"
          />
        </label>
        <label className="text-sm">
          <span className="text-xs text-slate-500 font-medium block mb-1">Name</span>
          <input className={INPUT_CLS} value={name} onChange={(e) => setName(e.target.value)} placeholder="my-dataset" />
        </label>
        <label className="text-sm">
          <span className="text-xs text-slate-500 font-medium block mb-1">
            Description <span className="text-slate-500">(optional)</span>
          </span>
          <input className={INPUT_CLS} value={description} onChange={(e) => setDescription(e.target.value)} />
        </label>
        {kind === 'images' && (
          <label className="text-sm">
            <span className="text-xs text-slate-500 font-medium block mb-1">
              Resize to (pixels per side) <span className="text-slate-500">· grayscale</span>
            </span>
            <NumberField value={imageSize} min={4} max={64} step={1} onChange={setImageSize} className={INPUT_CLS} />
          </label>
        )}
      </div>
      <div className="mt-3 flex justify-end">
        <PrimaryButton onClick={handleUpload} disabled={!file || !name.trim() || uploading}>
          <span className="flex items-center gap-2">
            {uploading ? <Loader2 size={16} className="animate-spin" /> : <Upload size={16} />}
            {uploading ? 'Uploading…' : 'Upload'}
          </span>
        </PrimaryButton>
      </div>

      {/* Existing datasets ------------------------------------------------ */}
      <div className="mt-6 pt-5 border-t border-slate-200">
        <div className="flex items-center justify-between mb-2">
          <span className="text-sm font-medium text-slate-700 flex items-center gap-2">
            <Database size={15} className="text-slate-500" /> Your datasets
          </span>
          <button
            type="button"
            onClick={refresh}
            disabled={loading}
            className="text-xs text-slate-500 hover:text-slate-800 flex items-center gap-1"
          >
            <RefreshCw size={13} className={loading ? 'animate-spin' : ''} /> Refresh
          </button>
        </div>
        {datasets.length === 0 ? (
          <p className="text-sm text-slate-500 italic">{loading ? 'Loading…' : 'No datasets yet. Upload one above.'}</p>
        ) : (
          <div className="space-y-1.5 max-h-64 overflow-auto pr-1">
            {datasets.map((d) => {
              const active = selectedDataset?.id === d.id;
              const image = Boolean(parseDatasetSchema(d).image);
              return (
                <div
                  key={d.id}
                  className={`flex items-center gap-3 rounded-lg border px-3 py-2 text-sm transition-colors ${
                    active ? 'border-blue-500 bg-blue-50' : 'border-slate-200 bg-white hover:border-slate-300'
                  }`}
                >
                  <button type="button" onClick={() => select(d)} className="flex-1 min-w-0 flex items-center gap-2 text-left">
                    {active ? (
                      <CheckCircle2 size={15} className="text-blue-600 shrink-0" />
                    ) : image ? (
                      <ImageIcon size={15} className="text-slate-500 shrink-0" />
                    ) : (
                      <FileSpreadsheet size={15} className="text-slate-500 shrink-0" />
                    )}
                    <span className={`font-medium truncate ${active ? 'text-blue-800' : 'text-slate-800'}`}>{d.name}</span>
                    <span className="text-xs text-slate-500 shrink-0">
                      {image ? 'images' : d.file_format}
                      {d.row_count != null ? ` · ${d.row_count} rows` : ''}
                    </span>
                  </button>
                  <button
                    type="button"
                    onClick={() => setPendingDelete(d)}
                    className="text-slate-500 hover:text-rose-600 transition-colors"
                    title="Delete dataset"
                  >
                    <Trash2 size={15} />
                  </button>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Columns -------------------------------------------------------------- */}
      {selectedDataset && (
        <div className="mt-6 pt-5 border-t border-slate-200 space-y-4">
          {isImage ? (
            <div className="text-sm text-slate-600 bg-slate-50 border border-slate-200 rounded-lg px-3.5 py-2.5">
              Image dataset · {selectedSchema.image?.side}×{selectedSchema.image?.side} pixels ·{' '}
              {quickSplit.featureColumns.length} pixel features · target <code className="font-mono">{quickSplit.targetColumn || '—'}</code>
              {selectedSchema.num_classes ? ` · ${selectedSchema.num_classes} classes` : ''}
              {selectedSchema.class_names?.length ? (
                <span className="block text-xs text-slate-500 mt-1">Classes: {selectedSchema.class_names.join(', ')}</span>
              ) : null}
            </div>
          ) : columns.length > 0 ? (
            <>
              <label className="text-sm block max-w-xs">
                <span className="text-xs text-slate-500 font-medium block mb-1">Target column</span>
                <select className={INPUT_CLS} value={quickSplit.targetColumn} onChange={(e) => setTarget(e.target.value)}>
                  <option value="">Select…</option>
                  {columns.map((c) => (
                    <option key={c} value={c}>
                      {c}
                    </option>
                  ))}
                </select>
              </label>
              <div>
                <div className="flex items-center justify-between mb-1">
                  <span className="text-xs text-slate-500 font-medium">
                    Feature columns ({quickSplit.featureColumns.length} selected · numeric only)
                  </span>
                  <span className="flex gap-3 text-xs">
                    <button
                      type="button"
                      className="text-blue-600 hover:text-blue-800"
                      onClick={() =>
                        onQuickSplitChange({ ...quickSplit, featureColumns: columns.filter((c) => c !== quickSplit.targetColumn) })
                      }
                    >
                      All
                    </button>
                    <button
                      type="button"
                      className="text-slate-500 hover:text-slate-800"
                      onClick={() => onQuickSplitChange({ ...quickSplit, featureColumns: [] })}
                    >
                      None
                    </button>
                  </span>
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {columns
                    .filter((c) => c !== quickSplit.targetColumn)
                    .map((c) => {
                      const on = quickSplit.featureColumns.includes(c);
                      return (
                        <button
                          key={c}
                          type="button"
                          onClick={() => toggleFeature(c)}
                          className={`px-2.5 py-1 rounded-md text-xs font-medium border transition-colors ${
                            on
                              ? 'bg-blue-50 text-blue-700 border-blue-300'
                              : 'bg-white text-slate-500 border-slate-200 hover:border-slate-300'
                          }`}
                        >
                          {c}
                        </button>
                      );
                    })}
                </div>
              </div>
            </>
          ) : (
            <>
              <p className="text-xs text-amber-800 bg-amber-50 border border-amber-200 rounded-lg px-3.5 py-2.5">
                Column names for this dataset are not known to the browser (Parquet, or uploaded elsewhere). Type them
                exactly as they appear in the file.
              </p>
              <label className="text-sm block max-w-xs">
                <span className="text-xs text-slate-500 font-medium block mb-1">Target column</span>
                <input
                  className={INPUT_CLS}
                  value={quickSplit.targetColumn}
                  onChange={(e) => onQuickSplitChange({ ...quickSplit, targetColumn: e.target.value.trim() })}
                  placeholder="label"
                />
              </label>
              <div className="text-sm">
                <span className="text-xs text-slate-500 font-medium block mb-1">Feature columns (numeric only)</span>
                <TagInput
                  values={quickSplit.featureColumns}
                  onChange={(featureColumns) => onQuickSplitChange({ ...quickSplit, featureColumns })}
                />
              </div>
            </>
          )}
        </div>
      )}

      <ConfirmDialog
        open={pendingDelete !== null}
        title="Delete dataset?"
        message={`"${pendingDelete?.name ?? ''}" will be removed from the list and its stored file deleted.`}
        onConfirm={confirmDelete}
        onCancel={() => setPendingDelete(null)}
      />
    </Card>
  );
}

