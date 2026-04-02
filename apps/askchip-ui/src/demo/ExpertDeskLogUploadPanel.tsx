import type { ChangeEvent } from 'react';
import type { ExpertDeskUploadedLogMetadata, ExpertDeskUploadedLogSource } from './types';

type ExpertDeskLogUploadPanelProps = {
  files: ExpertDeskUploadedLogMetadata[];
  uploadSource: ExpertDeskUploadedLogSource;
  onAddFiles: (files: FileList, source: ExpertDeskUploadedLogSource) => void;
  title?: string;
  compact?: boolean;
  helperNote?: string;
};

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
  uploadSource,
  onAddFiles,
  title = 'Log file upload',
  compact = false,
  helperNote,
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
        Frontend-local only for now: files are not uploaded to backend storage or parsed by AskChip in this phase.
      </p>
      {helperNote ? <p className="mt-1 text-xs leading-5 text-slate-600">{helperNote}</p> : null}
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
    </section>
  );
}
