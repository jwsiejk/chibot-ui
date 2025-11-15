// Entry point for the AskChip client bundle.
//
// The existing app.js attaches all runtime APIs to window.*. We simply import it
// so esbuild bundles the side effects while keeping the global shape intact.
import './app.js';
