import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { GeneratedDeckHistoryList } from '../session/GeneratedDeckHistoryList';

describe('GeneratedDeckHistoryList', () => {
  it('renders recent generated decks with safe download metadata only', () => {
    render(
      <GeneratedDeckHistoryList
        generatedDeckHistory={[
          {
            id: '2',
            file_name: 'deck-2.pptx',
            download_url: '/api/presentations/deck-2.pptx',
            format: 'pptx',
            theme_id: 'professional_light',
            generated_at: '2026-05-26T12:00:00.000Z',
            title: 'Deck Two',
          },
          {
            id: '1',
            file_name: 'deck-1.pptx',
            download_url: '/api/presentations/deck-1.pptx',
            format: 'pptx',
          },
        ]}
      />,
    );

    expect(screen.getByText('Generated decks')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Download' })).toHaveAttribute('href', '/api/presentations/deck-2.pptx');
    expect(screen.queryByText('/tmp/internal/deck-2.pptx')).not.toBeInTheDocument();
  });
});
