import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { GeneratedPresentationDownloadPill } from '../session/GeneratedPresentationDownloadPill';

describe('GeneratedPresentationDownloadPill', () => {
  it('renders only when generated status includes safe download metadata', () => {
    const { rerender } = render(<GeneratedPresentationDownloadPill generatedPresentation={{ status: 'generating', format: 'pptx' }} />);
    expect(screen.queryByLabelText('generated presentation download')).not.toBeInTheDocument();

    rerender(<GeneratedPresentationDownloadPill generatedPresentation={{ status: 'generated', format: 'pptx', file_name: 'deck.pptx', file_path: '/tmp/internal/deck.pptx', download_url: '/api/presentations/deck.pptx' }} />);
    expect(screen.getByText('PowerPoint ready')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Download' })).toHaveAttribute('href', '/api/presentations/deck.pptx');
    expect(screen.getByRole('link', { name: 'Download' })).toHaveAttribute('download', 'deck.pptx');
    expect(screen.queryByText('/tmp/internal/deck.pptx')).not.toBeInTheDocument();
  });
});
