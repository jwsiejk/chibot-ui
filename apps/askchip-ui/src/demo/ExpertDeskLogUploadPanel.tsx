import type { ChangeEvent } from 'react';
import type { ExpertDeskUploadedLogMetadata, ExpertDeskUploadedLogSource } from './types';
import type { VmwareArtifactRecord } from '../types/contract';

type ExpertDeskLogUploadPanelProps = {
  files: ExpertDeskUploadedLogMetadata[];
  backendArtifacts?: VmwareArtifactRecord[];
  uploadSource: ExpertDeskUploadedLogSource;
  onAddFiles: (files: FileList, source: ExpertDeskUploadedLogSource) => void;
  title?: string;
  compact?: boolean;
  helperNote?: string;
  uploadErrorMessage?: string | null;
};

function getArtifactStatusLabel(status: VmwareArtifactRecord['status']): string {
  switch (status) {
    case 'parsed_supported':
      return 'Parsed (supported log)';
    case 'uploaded_unsupported':
      return 'Stored (unsupported file type)';
    case 'parse_failed':
      return 'Parse failed (supported log)';
    case 'metadata_only':
      return 'Metadata-only context (not an upload result)';
    case 'uploaded_supported_unparsed':
      return 'Reserved async state (not used in current sync uploads)';
    default:
      return status;
  }
}

function formatBytes(size: number): string {
  if (size < 1024) {
    return `${size} B`;
  }
  if (size < 1024 * 1024) {
    return `${(size / 1024).toFixed(1)} KB`;
  }
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

export function ExpertDeskLogUploadPanel({
  files,
  backendArtifacts = [],
  uploadSource,
  onAddFiles,
  title = 'Log file upload',
  compact = false,
  helperNote,
  uploadErrorMessage,
}: ExpertDeskLogUploadPanelProps) {
  const handleChange = (event: ChangeEvent<HTMLInputElement>) => {
    if (!event.target.files || event.target.files.length === 0) {
      return;
    }

    onAddFiles(event.target.files, uploadSource);
    event.target.value = '';
  };

  return (
    <section className={`rounded-2xl border border-slate-200 bg-slate-50 ${compact ? 'p-3' : 'p-4'}`}>
      <p className="text-sm font-semibold text-slate-900">{title}</p>
      <p className="mt-1 text-xs leading-5 text-slate-600">
        Intake files are frontend-local. Live-session uploads are sent to backend for supported VMware parser checks.
      </p>
      {helperNote ? <p className="mt-1 text-xs leading-5 text-slate-600">{helperNote}</p> : null}
      {uploadErrorMessage ? <p className="mt-1 text-xs leading-5 text-rose-600">{uploadErrorMessage}</p> : null}
      <label className="mt-3 inline-flex cursor-pointer rounded-full border border-indigo-300 bg-indigo-50 px-4 py-2 text-xs font-semibold text-indigo-700 hover:bg-indigo-100">
        Add log files
        <input
          type="file"
          multiple
          className="sr-only"
          onChange={handleChange}
          aria-label={`Upload log files (${uploadSource})`}
        />
      </label>
      <div className="mt-3 space-y-2">
        {files.length === 0 ? (
          <p className="text-xs text-slate-500">No files added yet.</p>
        ) : (
          files.map((file, index) => (
            <article key={`${file.name}-${file.uploaded_at}-${index}`} className="rounded-xl border border-slate-200 bg-white px-3 py-2">
              <p className="text-sm font-medium text-slate-900">{file.name}</p>
              <p className="mt-1 text-xs text-slate-600">
                {formatBytes(file.size)} · {file.type || 'type not provided'} · {new Date(file.uploaded_at).toLocaleString()} ·{' '}
                {file.uploaded_in === 'intake' ? 'added in intake' : 'added in live session'}
              </p>
            </article>
          ))
        )}
      </div>
      {backendArtifacts.length > 0 ? (
        <div className="mt-3 space-y-2">
          <p className="text-xs font-semibold text-slate-700">Backend artifact ingestion status</p>
          {backendArtifacts.map((artifact) => (
            <article key={artifact.id} className="rounded-xl border border-slate-200 bg-white px-3 py-2">
              <p className="text-sm font-medium text-slate-900">{artifact.filename}</p>
              <p className="mt-1 text-xs text-slate-600">
                status: {getArtifactStatusLabel(artifact.status)} · type: {artifact.artifact_type || 'unknown'} · uploaded: {new Date(artifact.uploaded_at).toLocaleString()}
              </p>
              {artifact.parse_error ? <p className="mt-1 text-xs text-rose-600">parse_error: {artifact.parse_error}</p> : null}
            </article>
          ))}
        </div>
      ) : null}
    </section>
  );
}
