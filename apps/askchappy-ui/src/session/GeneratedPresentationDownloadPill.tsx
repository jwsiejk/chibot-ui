import React from 'react';
import type { CreatePresentationsGeneratedPresentationState } from '../../../../shared/contracts/createPresentationsMode';

export const GeneratedPresentationDownloadPill = ({
  generatedPresentation,
}: {
  generatedPresentation?: CreatePresentationsGeneratedPresentationState;
}) => {
  if (generatedPresentation?.status !== 'generated') {
    return null;
  }

  if (!generatedPresentation.download_url) {
    return null;
  }

  return (
    <section className="generated-presentation-pill" aria-label="generated presentation download">
      <p className="generated-presentation-pill-copy">PowerPoint ready</p>
      <a
        className="btn secondary"
        href={generatedPresentation.download_url}
        download={generatedPresentation.file_name ?? true}
      >
        Download
      </a>
    </section>
  );
};
