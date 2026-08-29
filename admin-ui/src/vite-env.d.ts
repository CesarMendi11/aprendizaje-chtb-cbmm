/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_ADMIN_API_MODE?: "demo" | "live";
  readonly VITE_ADMIN_API_TARGET?: string;
}
