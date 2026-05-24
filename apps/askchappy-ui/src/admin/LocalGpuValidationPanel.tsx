import React, { useEffect, useState } from 'react';
import type { LocalGpuValidationReport } from '../../../../shared/contracts/gpu';
import { getLocalGpuValidationReport } from '../../../../services/askchappy-api/src/api/server';

export const LocalGpuValidationPanel = () => {
  const [report, setReport] = useState<LocalGpuValidationReport | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let mounted = true;
    void getLocalGpuValidationReport()
      .then((next) => {
        if (mounted) setReport(next);
      })
      .catch(() => {
        if (mounted) setError('Unable to load local GPU validation status.');
      });

    return () => {
      mounted = false;
    };
  }, []);

  return (
    <section aria-label="local gpu validation">
      <h2>Local GPU Validation</h2>
      <p>Admin-only status view based on local service health/config APIs. This panel does not execute prompts, transcription, or TTS synthesis.</p>
      {error ? <p>{error}</p> : null}
      {!report ? <p>Loading local GPU validation status…</p> : null}
      {report ? (
        <>
          <p>Generated at: {report.generated_at}</p>
          {report.services.map((entry) => (
            <article key={entry.service}>
              <h3>{entry.service}</h3>
              <p>Status: {entry.status}</p>
              <p>Reason: {entry.reason}</p>
              {entry.suggested_commands.length ? <p>Suggested commands: {entry.suggested_commands.join(' | ')}</p> : null}
            </article>
          ))}
          <h3>Windows/NVIDIA manual confirmation</h3>
          <ul>
            {report.manual_guidance.map((line) => <li key={line}>{line}</li>)}
          </ul>
        </>
      ) : null}
    </section>
  );
};
