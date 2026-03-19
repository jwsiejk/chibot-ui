export type FinalizeReason = 'error' | 'close';

export function createConnectionFinalizer(options: {
  isCurrentSocket: () => boolean;
  clearCurrentSocket: () => void;
  onError: () => void;
  onClose: () => void;
}) {
  let finalized = false;

  return (reason: FinalizeReason) => {
    if (finalized) {
      return;
    }
    finalized = true;

    if (options.isCurrentSocket()) {
      options.clearCurrentSocket();
    }

    if (reason === 'error') {
      options.onError();
      return;
    }

    options.onClose();
  };
}
