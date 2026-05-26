export type DdnStartingPoint = 'use_case' | 'product_solution' | 'recommend';
export type DdnUseCase =
  | 'ai_genai_infrastructure'
  | 'life_sciences_genomics'
  | 'healthcare_imaging_clinical'
  | 'hpc_research_computing'
  | 'data_analytics_scale'
  | 'private_ai_secure_ai'
  | 'csp_ai_cloud';
export type DdnProductFocus = 'infinia' | 'ai400x' | 'exascaler' | 'dip' | 'intelliflash' | 'insight' | 'recommend';
export type DdnAudience = 'executive' | 'infrastructure' | 'ai_data_platform' | 'research_scientific' | 'storage_hpc' | 'partner_enablement';
export type DdnDepth = 'short_exec' | 'standard' | 'technical';

export const DEPTH_SLIDE_COUNT: Record<DdnDepth, number> = { short_exec: 5, standard: 7, technical: 9 };
export const labelMap = {
  startingPoint: { use_case: 'Use case', product_solution: 'Product / solution', recommend: 'Recommend best fit' },
  useCase: {
    ai_genai_infrastructure: 'AI / GenAI infrastructure',
    life_sciences_genomics: 'Life sciences research and genomics',
    healthcare_imaging_clinical: 'Healthcare imaging and clinical data',
    hpc_research_computing: 'HPC / research computing',
    data_analytics_scale: 'Data analytics at scale',
    private_ai_secure_ai: 'Private AI / secure AI platform',
    csp_ai_cloud: 'Cloud service provider / AI cloud',
  },
  product: { infinia: 'Infinia', ai400x: 'AI400X / AI400X2 / AI400X3', exascaler: 'EXAScaler', dip: 'Data Intelligence Platform', intelliflash: 'IntelliFlash', insight: 'Insight / data intelligence', recommend: 'Recommend the best fit' },
  audience: { executive: 'Executive / CIO / CTO', infrastructure: 'Infrastructure leadership', ai_data_platform: 'AI / data platform team', research_scientific: 'Research / scientific computing team', storage_hpc: 'Storage / HPC technical team', partner_enablement: 'Partner sales / SE enablement' },
  depth: { short_exec: 'Short executive deck', standard: 'Standard customer meeting', technical: 'Technical detail included' },
};

export const inferUseCaseFromProduct = (product: Exclude<DdnProductFocus, 'recommend'>): DdnUseCase => ({
  infinia: 'ai_genai_infrastructure', ai400x: 'ai_genai_infrastructure', exascaler: 'hpc_research_computing', dip: 'ai_genai_infrastructure', intelliflash: 'data_analytics_scale', insight: 'data_analytics_scale',
}[product]);

export const recommendFocus = (useCase: DdnUseCase) => useCase === 'life_sciences_genomics' ? 'Infinia + Data Intelligence Platform' : 'DDN solution aligned to this use case';
