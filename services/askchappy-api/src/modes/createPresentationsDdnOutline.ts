import type { CreatePresentationsOutlineSlide, CreatePresentationsOutlineState } from '../../../../shared/contracts/createPresentationsMode';
import type { DdnDepth, DdnUseCase } from './createPresentationsDdnPresets';

type SlidePreset = Pick<CreatePresentationsOutlineSlide, 'title' | 'objective' | 'key_points'>;

const lifeSciencesSlides: Record<DdnDepth, SlidePreset[]> = {
  short_exec: [
    {
      title: 'Why Life Sciences Data Strategy Is Urgent',
      objective: 'Establish why genomics and translational research teams must modernize data operations now.',
      key_points: ['Sequencers are outpacing legacy storage and metadata workflows', 'AI model quality depends on governed, multi-omic data readiness'],
    },
    {
      title: 'Current Genomics Program Friction',
      objective: 'Pinpoint where platform and pipeline bottlenecks slow discovery and clinical translation.',
      key_points: ['Variant interpretation cycles are delayed by fragmented data estates', 'Cross-site collaboration suffers when data movement and access controls are inconsistent'],
    },
    {
      title: 'DDN Strategy for AI-Driven Biology',
      objective: 'Show how DDN aligns high-performance data infrastructure to modern biology workflows.',
      key_points: ['Infinia unifies performance, data services, and policy control for research workloads', 'Data Intelligence Platform accelerates data discovery, lineage, and governance for AI use cases'],
    },
    {
      title: 'Recommended First 90-Day Program',
      objective: 'Define a pragmatic execution plan that demonstrates measurable value quickly.',
      key_points: ['Start with one high-impact workflow such as cohort-scale variant analysis', 'Track outcomes: pipeline runtime, researcher wait time, and cost per analysis'],
    },
    {
      title: 'Executive Decision and Next Actions',
      objective: 'Align sponsors on investment path, success criteria, and governance ownership.',
      key_points: ['Approve architecture workshop and success metrics baseline', 'Set joint business and scientific milestones for pilot-to-production expansion'],
    },
  ],
  standard: [
    {
      title: 'Why Life Sciences Data Infrastructure Matters Now',
      objective: 'Frame the strategic urgency for scalable, compliant data infrastructure in genomics and biopharma research.',
      key_points: ['Sequencing throughput, imaging resolution, and assay diversity are compounding data growth', 'Therapeutic timelines now depend on AI-ready data quality and accessibility'],
    },
    {
      title: 'Genomics and Research Data Challenges',
      objective: 'Detail operational pain points that limit speed, reproducibility, and cross-functional collaboration.',
      key_points: ['Siloed storage and tooling create handoff delays between bioinformatics and data science teams', 'Data governance controls are often bolted on, increasing compliance risk and analyst friction'],
    },
    {
      title: 'What Changes with AI-Driven Biology',
      objective: 'Explain how multimodal AI and foundation models reshape infrastructure requirements.',
      key_points: ['Model development requires fast access to harmonized sequence, phenotype, and imaging datasets', 'Continuous retraining workflows demand lineage tracking, curation, and scalable throughput'],
    },
    {
      title: 'DDN Point of View',
      objective: 'Present DDN architecture principles for life sciences environments balancing performance and governance.',
      key_points: ['Infinia provides high-throughput data infrastructure optimized for mixed HPC and AI pipelines', 'Data Intelligence Platform connects metadata, policy, and data lifecycle across research domains'],
    },
    {
      title: 'Recommended DDN Solution Focus',
      objective: 'Translate architecture into a concrete solution pattern for genomics and translational workflows.',
      key_points: ['Anchor on Infinia + Data Intelligence Platform for scalable ingestion, analysis, and governed sharing', 'Prioritize workflow domains where latency and reproducibility directly impact program outcomes'],
    },
    {
      title: 'Partner / Reseller Value',
      objective: 'Clarify how partners accelerate adoption with domain-specific services and integration leadership.',
      key_points: ['Package migration, workflow modernization, and validation services for regulated research teams', 'Create recurring value through optimization, enablement, and operational governance services'],
    },
    {
      title: 'Recommended Next Steps',
      objective: 'Define an execution sequence from assessment to pilot and scaled deployment.',
      key_points: ['Run a joint discovery workshop to baseline workloads, data topology, and compliance obligations', 'Launch a time-boxed pilot with agreed technical and business success metrics'],
    },
  ],
  technical: [
    {
      title: 'Life Sciences Workload and Data Growth Profile',
      objective: 'Quantify pipeline characteristics that drive infrastructure design decisions.',
      key_points: ['Concurrent NGS alignment, variant calling, and multimodal training create bursty demand', 'Hot data tiers must serve low-latency random access and sustained streaming workloads'],
    },
    {
      title: 'Pipeline Bottlenecks Across the Data Path',
      objective: 'Identify where throughput, orchestration, and metadata gaps constrain productivity.',
      key_points: ['I/O contention and namespace fragmentation increase queue time for shared compute clusters', 'Manual dataset curation introduces reproducibility drift between development and production runs'],
    },
    {
      title: 'AI-Driven Biology Technical Requirements',
      objective: 'Specify platform capabilities needed for retrieval, training, and inference workflows.',
      key_points: ['Unified metadata, lineage, and governance are required for trustworthy model development', 'Storage and data services must support both POSIX-heavy and object-native toolchains'],
    },
    {
      title: 'DDN Architecture Point of View',
      objective: 'Map DDN reference architecture to research, translational, and clinical-adjacent environments.',
      key_points: ['Infinia delivers parallel performance, predictable throughput, and resilient operations at scale', 'Data Intelligence Platform operationalizes policy, indexing, and discoverability across data domains'],
    },
    {
      title: 'Recommended DDN Solution Blueprint',
      objective: 'Propose a phased architecture blueprint with clear integration boundaries.',
      key_points: ['Start with targeted workflow domains and federate access before broad migration', 'Automate policy enforcement and lifecycle controls early to reduce downstream compliance rework'],
    },
    {
      title: 'Validation / PoC Success Criteria',
      objective: 'Define measurable benchmarks for technical validation and production readiness.',
      key_points: ['Measure end-to-end runtime reduction, I/O stability, and dataset onboarding velocity', 'Include governance auditability and operations runbook maturity in exit criteria'],
    },
    {
      title: 'Partner / Reseller Delivery Model',
      objective: 'Outline delivery responsibilities across architecture, implementation, and enablement phases.',
      key_points: ['Assign partner ownership for integration automation and workflow certification artifacts', 'Embed joint knowledge transfer for platform operations and continuous optimization'],
    },
    {
      title: 'Technical Next Steps',
      objective: 'Sequence immediate engineering actions to de-risk rollout.',
      key_points: ['Schedule architecture deep-dive and data path assessment with stakeholder SMEs', 'Finalize pilot scope, observability plan, and production promotion gates'],
    },
  ],
};

const fallbackSlides: SlidePreset[] = [
  {
    title: 'Use-Case Drivers and Business Stakes',
    objective: 'Align on the business outcomes, risk posture, and timing pressures shaping the data strategy.',
    key_points: ['Prioritize outcomes tied to revenue, service quality, and operational resilience', 'Document constraints such as compliance, budget guardrails, and delivery deadlines'],
  },
  {
    title: 'Current-State Constraints',
    objective: 'Surface the largest technical and organizational blockers in the current environment.',
    key_points: ['Identify performance bottlenecks, data silos, and process handoff friction', 'Separate immediate reliability gaps from structural platform limitations'],
  },
  {
    title: 'Target Platform Requirements',
    objective: 'Define the capabilities required to support AI, analytics, and high-throughput workloads.',
    key_points: ['Specify throughput, latency, data governance, and interoperability requirements', 'Set measurable service objectives for both engineering and business teams'],
  },
  {
    title: 'DDN Solution Alignment',
    objective: 'Map DDN platform capabilities to the prioritized requirements and constraints.',
    key_points: ['Position the recommended DDN stack against the most critical workload demands', 'Highlight integration considerations with existing compute, cloud, and security controls'],
  },
  {
    title: 'Partner Execution Plan',
    objective: 'Clarify how delivery partners accelerate rollout while reducing implementation risk.',
    key_points: ['Define partner-led workstreams for migration, automation, and enablement', 'Establish governance cadence and decision checkpoints across teams'],
  },
  {
    title: 'Pilot Scope and Success Metrics',
    objective: 'Agree on pilot boundaries, ownership, and evidence needed for scale-out approval.',
    key_points: ['Pick a high-impact workload with clear baseline metrics and expected improvements', 'Set scale criteria for performance, reliability, governance, and total cost outcomes'],
  },
];

export const buildDdnOutline = (useCase: DdnUseCase, depth: DdnDepth, nowIso: string): CreatePresentationsOutlineState => {
  const presets = useCase === 'life_sciences_genomics' ? lifeSciencesSlides[depth] : fallbackSlides;
  const slides: CreatePresentationsOutlineSlide[] = presets.map((preset, idx) => ({
    slide_number: idx + 1,
    title: preset.title,
    objective: preset.objective,
    key_points: [...preset.key_points],
  }));
  return { status: 'outline_review', slides, created_at: nowIso, updated_at: nowIso };
};
