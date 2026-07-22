import { createAutoBackend } from './auto-backend.mjs';
import { createD8Backend } from './d8-backend.mjs';
import { createProfileBackend } from './profile-backend.mjs';

function createBackend(options, runtime) {
  const profile = createProfileBackend(runtime);
  const d8 = createD8Backend({ ...runtime, d8Path: options.d8Path });
  if (options.backend === 'profile') return profile;
  if (options.backend === 'd8') return d8;
  return createAutoBackend(profile, d8);
}

export { createBackend };
