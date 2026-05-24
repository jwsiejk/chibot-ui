export const LOCAL_GPU_VALIDATION_SERVICES = ['ollama', 'faster_whisper', 'kokoro_onnx'] as const;

export type LocalGpuValidationService = (typeof LOCAL_GPU_VALIDATION_SERVICES)[number];

export const LOCAL_GPU_VALIDATION_STATUSES = [
  'gpu_confirmed',
  'cpu_only',
  'unknown',
  'runtime_unreachable',
  'not_configured',
  'not_applicable',
] as const;

export type LocalGpuValidationStatus = (typeof LOCAL_GPU_VALIDATION_STATUSES)[number];

export type LocalGpuServiceValidation = {
  service: LocalGpuValidationService;
  status: LocalGpuValidationStatus;
  reason: string;
  suggested_commands: string[];
};

export type LocalGpuValidationReport = {
  generated_at: string;
  services: LocalGpuServiceValidation[];
  manual_guidance: string[];
};
