import { useState } from 'react';

export function useShellPanels() {
  const [showUtilityRail, setShowUtilityRail] = useState(true);
  const [showDiagnostics, setShowDiagnostics] = useState(true);

  return {
    showUtilityRail,
    showDiagnostics,
    toggleUtilityRail: () => setShowUtilityRail((current) => !current),
    toggleDiagnostics: () => setShowDiagnostics((current) => !current),
  };
}
