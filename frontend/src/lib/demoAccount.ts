export interface DemoAccount {
  tenantId: string;
  email: string;
  password: string;
}

// The local demo's fixed account (see backend/scripts/seed_demo.py), offered on the login page
// so nobody has to copy ids around. Only ever available in the dev server (`import.meta.env.DEV`)
// and only when the VITE_DEMO_* variables are set (frontend/.env.development); a production
// build never includes or offers it.
export function getDemoAccount(
  env: { DEV?: boolean; VITE_DEMO_TENANT_ID?: string; VITE_DEMO_EMAIL?: string; VITE_DEMO_PASSWORD?: string } = import.meta.env
): DemoAccount | null {
  if (!env.DEV) return null;
  const { VITE_DEMO_TENANT_ID: tenantId, VITE_DEMO_EMAIL: email, VITE_DEMO_PASSWORD: password } = env;
  if (!tenantId || !email || !password) return null;
  return { tenantId, email, password };
}
