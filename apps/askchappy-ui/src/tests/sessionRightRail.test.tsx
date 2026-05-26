import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { SessionRightRail } from '../session/SessionRightRail';

const noop = vi.fn();

describe('SessionRightRail presentation export state', () => {
  it('hides presentation export section when generatedPresentation is absent', () => {
    render(<SessionRightRail activeMode="open_qa" onSelectMode={noop} />);
    expect(screen.queryByLabelText('presentation export status')).not.toBeInTheDocument();
  });

  it('shows generated state details and hides file_path', () => {
    render(<SessionRightRail activeMode="create_presentations" onSelectMode={noop} generatedPresentation={{ status: 'generated', format: 'pptx', file_name: 'deck.pptx', file_path: '/tmp/internal/deck.pptx', download_url: '/api/presentations/deck.pptx' }} />);
    expect(screen.getByText('Status: Ready')).toBeInTheDocument();
    expect(screen.getByText('Type: PPTX')).toBeInTheDocument();
    expect(screen.getByText('File: deck.pptx')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Download PowerPoint' })).toHaveAttribute('href', '/api/presentations/deck.pptx');
    expect(screen.queryByText('/tmp/internal/deck.pptx')).not.toBeInTheDocument();
  });


  it('shows generated deck history list when available', () => {
    render(
      <SessionRightRail
        activeMode="create_presentations"
        onSelectMode={noop}
        generatedPresentation={{ status: 'generated', format: 'pptx', file_name: 'deck-latest.pptx', download_url: '/api/presentations/deck-latest.pptx' }}
        generatedDeckHistory={[
          { id: 'new', file_name: 'deck-latest.pptx', download_url: '/api/presentations/deck-latest.pptx', format: 'pptx', title: 'Latest deck' },
          { id: 'old', file_name: 'deck-old.pptx', download_url: '/api/presentations/deck-old.pptx', format: 'pptx' },
        ]}
      />,
    );
    expect(screen.getByText('Generated decks')).toBeInTheDocument();
    expect(screen.getAllByRole('link', { name: 'Download' })[0]).toHaveAttribute('href', '/api/presentations/deck-latest.pptx');
  });

  it('shows generating and error states', () => {
    const { rerender } = render(<SessionRightRail activeMode="create_presentations" onSelectMode={noop} generatedPresentation={{ status: 'generating', format: 'pptx' }} />);
    expect(screen.getByText('Status: Generating PPTX…')).toBeInTheDocument();

    rerender(<SessionRightRail activeMode="create_presentations" onSelectMode={noop} generatedPresentation={{ status: 'error', format: 'pptx', error_message: 'boom' }} />);
    expect(screen.getByRole('alert')).toHaveTextContent('Export failed: boom');
  });

  it('still supports selecting modes', () => {
    render(<SessionRightRail activeMode="open_qa" onSelectMode={noop} />);
    fireEvent.click(screen.getByRole('button', { name: 'Create Presentations' }));
    expect(noop).toHaveBeenCalledWith('create_presentations');
  });
});
