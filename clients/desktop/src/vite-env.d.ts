/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_DAPR_HTTP_PORT?: string;
  readonly VITE_BACKEND_URL?: string;
  readonly VITE_USE_DAPR?: string;
  readonly VITE_ENVIRONMENT?: string;
  readonly VITE_MODEL_NAME?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
