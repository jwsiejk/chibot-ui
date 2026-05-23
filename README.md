# AskChappy

AskChappy is local-first production software for DDN partner enablement workflows.

## Core docs
- V1 specification: `docs/ASKCHAPPY_V1_SPEC.md`
- Implementation contracts: `docs/IMPLEMENTATION_CONTRACTS.md`
- Build playbook: `docs/BUILD_PLAYBOOK.md`
- Local-first run guide: `docs/LOCAL_FIRST_RUN_GUIDE.md`
- Current implementation status: `docs/CURRENT_IMPLEMENTATION_STATUS.md`
- Local-first release checklist: `docs/LOCAL_FIRST_RELEASE_CHECKLIST.md`
- Dependency review: `docs/DEPENDENCY_REVIEW.md`
- Phase 10 cloned voice blocker note: `docs/PHASE10_CLONED_VOICE_BLOCKER_NOTE.md`

## Local verification
```bash
npm test
npm run lint
npm run verify
```

## Local run
```bash
npm run start
```

Default local runtime URL: `http://127.0.0.1:4173/chappy`.

## Terminology and route policy
- Deployment model: local-first, local production/local MVP.
- Retired `/demo*` and `/visual-session*` routes remain inactive historical routes.
