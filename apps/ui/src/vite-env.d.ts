/// <reference types="vite/client" />

declare const __HELIOS_BUILD_NUMBER__: string;

interface Window {
  __HELIOS_CONFIG__?: {
    apiUrl?: string;
  };
}
