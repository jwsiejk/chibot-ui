import type {
  CreatePresentationsDeckBrief,
  CreatePresentationsGeneratedDeckHistoryItem,
  CreatePresentationsGeneratedPresentationState,
  CreatePresentationsOutlineState,
} from '../../../../shared/contracts/createPresentationsMode';

const MAX_HISTORY_ITEMS = 10;

const toDeckTitle = (
  deckBrief: CreatePresentationsDeckBrief,
  outline: CreatePresentationsOutlineState,
): string | undefined => deckBrief.topic?.trim() || outline.slides[0]?.title?.trim() || undefined;

export const toGeneratedDeckHistoryItem = (
  generatedPresentation: CreatePresentationsGeneratedPresentationState,
  deckBrief: CreatePresentationsDeckBrief,
  outline: CreatePresentationsOutlineState,
): CreatePresentationsGeneratedDeckHistoryItem | null => {
  if (
    generatedPresentation.status !== 'generated'
    || !generatedPresentation.file_name
    || !generatedPresentation.download_url
  ) {
    return null;
  }

  return {
    id: generatedPresentation.generated_at ?? `${generatedPresentation.file_name}:${generatedPresentation.download_url}`,
    file_name: generatedPresentation.file_name,
    download_url: generatedPresentation.download_url,
    format: generatedPresentation.format,
    theme_id: generatedPresentation.theme_id,
    generated_at: generatedPresentation.generated_at,
    title: toDeckTitle(deckBrief, outline),
  };
};

export const appendGeneratedDeckHistory = (
  history: CreatePresentationsGeneratedDeckHistoryItem[] | undefined,
  entry: CreatePresentationsGeneratedDeckHistoryItem,
): CreatePresentationsGeneratedDeckHistoryItem[] => {
  const existing = Array.isArray(history) ? history : [];
  return [entry, ...existing.filter((item) => item.id !== entry.id)].slice(0, MAX_HISTORY_ITEMS);
};
