import { useEffect, useState } from 'react';

const DIAGNOSTICS_KEY = 'askchip-ui.show-diagnostics';
const UTILITY_KEY = 'askchip-ui.show-utility-rail';

function loadFlag(key: string, fallback: boolean): boolean {
  const value = window.localStorage.getItem(key);
  return value === null ? fallback : value === 'true';
}

export function useShellPanels() {
  const [showUtilityRail, setShowUtilityRail] = useState(() => loadFlag(UTILITY_KEY, true));
  const [showDiagnostics, setShowDiagnostics] = useState(() => loadFlag(DIAGNOSTICS_KEY, true));

  useEffect(() => {
    window.localStorage.setItem(UTILITY_KEY, String(showUtilityRail));
  }, [showUtilityRail]);

  useEffect(() => {
    window.localStorage.setItem(DIAGNOSTICS_KEY, String(showDiagnostics));
  }, [showDiagnostics]);

  return {
    showUtilityRail,
    showDiagnostics,
    toggleUtilityRail: () => setShowUtilityRail((current) => !current),
    toggleDiagnostics: () => setShowDiagnostics((current) => !current),
  };
}
