import type { CreatePresentationsModeEvent, CreatePresentationsPptxThemeId } from '../../../../shared/contracts/createPresentationsMode';
import type { AskChappySession } from '../sessions/sessionStore';
import { appendGeneratedDeckHistory, toGeneratedDeckHistoryItem } from './createPresentationsDeckHistory';
import { buildDdnOutline } from './createPresentationsDdnOutline';
import { DEPTH_SLIDE_COUNT, inferUseCaseFromProduct, labelMap, recommendFocus, type DdnAudience, type DdnDepth, type DdnProductFocus, type DdnStartingPoint, type DdnUseCase } from './createPresentationsDdnPresets';

export type GuidedAssistantResponse = { text: string; spokenText?: string };
type PptxGenerationResult = { fileName: string; downloadUrl: string; generatedAt?: string; themeId?: CreatePresentationsPptxThemeId; filePath?: string };

type DdnState = {
  stage?: 'starting'|'use_case'|'product'|'recommend'|'audience'|'customer'|'depth'|'notes'|'review'|'edit';
  startingPoint?: DdnStartingPoint; useCase?: DdnUseCase; product?: DdnProductFocus; audience?: DdnAudience; customer?: string; depth?: DdnDepth; notes?: string;
};
const answer=(text:string,spokenText?:string)=>({text,spokenText});
const now=()=>new Date().toISOString();
const n=(s:string)=>s.trim().toLowerCase();
const nextEvent=(event: Omit<CreatePresentationsModeEvent,'id'|'ts'>):CreatePresentationsModeEvent=>({id:`evt_${crypto.randomUUID()}`,ts:now(),...event});
const parse=(s:string,m:Record<string,string>)=>Object.entries(m).find(([,v])=>v.split('|').some(x=>n(x)===n(s)))?.[0];

const generatePptxRuntime = async (session: AskChappySession, state: any): Promise<PptxGenerationResult> => {
  if (typeof window !== 'undefined') {
    const response = await fetch('/api/presentations/generate', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ session_id: session.session_id, brief: state.deckBrief, outline: state.outline }),
    });
    if (!response.ok) throw new Error(`PPTX generation failed (${response.status})`);
    return response.json() as Promise<PptxGenerationResult>;
  }
  const { generatePptxFromApprovedOutline } = await import('./createPresentationsPptxGenerator');
  return generatePptxFromApprovedOutline(session.session_id, state.deckBrief, state.outline);
};

export const handleCreatePresentationsTurn = async (session: AskChappySession): Promise<GuidedAssistantResponse> => {
  const state = session.metadata.askchappy.create_presentations_state! as any;
  const ddn: DdnState = (state.ddn ??= {});
  state.generatedDeckHistory ??= [];
  const userText = [...session.transcript].reverse().find((m) => m.role === 'user')?.text?.trim() ?? '';
  state.events.push(nextEvent({ actor: 'user', step: state.step, kind: 'answer_recorded', text: userText }));
  const ask=(text:string,spoken?:string)=>{state.events.push(nextEvent({actor:'assistant',step:state.step,kind:'question_asked',text}));return answer(text,spoken)};
  const stage = ddn.stage ?? 'starting';

  switch (stage) {
    case 'starting': {
      ddn.stage='starting';
      const v=parse(userText,{use_case:'1|one|use case|customer use case',product_solution:'2|two|product|solution|product solution',recommend:'3|three|recommend|best fit|not sure'});
      if (!v) return ask('How do you want to build this DDN deck?\n\n1. Start with a customer use case\n2. Start with a DDN product / solution\n3. Recommend the best fit','Choose how you want to start.');
      ddn.startingPoint=v as DdnStartingPoint;
      ddn.stage=ddn.startingPoint==='use_case'?'use_case':ddn.startingPoint==='product_solution'?'product':'recommend';
      if (ddn.stage==='use_case') return ask('Choose a customer use case:\n\n1. AI / GenAI infrastructure\n2. Life sciences research and genomics\n3. Healthcare imaging and clinical data\n4. HPC / research computing\n5. Data analytics at scale\n6. Private AI / secure AI platform\n7. Cloud service provider / AI cloud','Choose the customer use case.');
      if (ddn.stage==='product') return ask('Choose the DDN product or solution focus:\n\n1. Infinia\n2. AI400X / AI400X2 / AI400X3\n3. EXAScaler\n4. Data Intelligence Platform\n5. IntelliFlash\n6. Insight / data intelligence\n7. Recommend the best fit','Choose the DDN solution focus.');
      return ask('What kind of customer conversation is this?\n\n1. Executive AI/data strategy\n2. Life sciences / genomics\n3. Technical architecture\n4. Partner enablement\n5. HPC / research computing\n6. General DDN overview','Choose the conversation type.');
    }
    case 'use_case': {
      const v=parse(userText,{ai_genai_infrastructure:'1|one|ai|genai|ai infrastructure',life_sciences_genomics:'2|two|life sciences|genomics|research genomics',healthcare_imaging_clinical:'3|three|healthcare imaging|clinical data|imaging',hpc_research_computing:'4|four|hpc|research computing',data_analytics_scale:'5|five|analytics|data analytics',private_ai_secure_ai:'6|six|private ai|secure ai',csp_ai_cloud:'7|seven|cloud service provider|ai cloud'});
      if (!v) return ask('Choose a customer use case:\n\n1. AI / GenAI infrastructure\n2. Life sciences research and genomics\n3. Healthcare imaging and clinical data\n4. HPC / research computing\n5. Data analytics at scale\n6. Private AI / secure AI platform\n7. Cloud service provider / AI cloud','Choose the customer use case.');
      ddn.useCase=v as DdnUseCase; ddn.stage='audience';
      return ask('Who is the audience?\n\n1. Executive / CIO / CTO\n2. Infrastructure leadership\n3. AI / data platform team\n4. Research / scientific computing team\n5. Storage / HPC technical team\n6. Partner sales / SE enablement','Choose the audience.');
    }
    case 'product': {
      const v=parse(userText,{infinia:'1|one|infinia',ai400x:'2|two|ai400x|ai400x2|ai400x3',exascaler:'3|three|exascaler',dip:'4|four|data intelligence platform|dip',intelliflash:'5|five|intelliflash',insight:'6|six|insight|data intelligence',recommend:'7|seven|recommend|best fit'});
      if(!v)return ask('Choose the DDN product or solution focus:\n\n1. Infinia\n2. AI400X / AI400X2 / AI400X3\n3. EXAScaler\n4. Data Intelligence Platform\n5. IntelliFlash\n6. Insight / data intelligence\n7. Recommend the best fit','Choose the DDN solution focus.');
      ddn.product=v as DdnProductFocus;
      if(ddn.product!=='recommend') ddn.useCase = inferUseCaseFromProduct(ddn.product);
      ddn.stage=ddn.product==='recommend'?'recommend':'audience';
      return ddn.stage==='recommend'
        ? ask('What kind of customer conversation is this?\n\n1. Executive AI/data strategy\n2. Life sciences / genomics\n3. Technical architecture\n4. Partner enablement\n5. HPC / research computing\n6. General DDN overview','Choose the conversation type.')
        : ask('Who is the audience?\n\n1. Executive / CIO / CTO\n2. Infrastructure leadership\n3. AI / data platform team\n4. Research / scientific computing team\n5. Storage / HPC technical team\n6. Partner sales / SE enablement','Choose the audience.');
    }
    case 'recommend': {
      const v=parse(userText,{executive:'1|executive ai/data strategy',life:'2|life sciences|genomics',technical:'3|technical architecture',partner:'4|partner enablement',hpc:'5|hpc|research computing',general:'6|general ddn overview|not sure'});
      if(!v)return ask('What kind of customer conversation is this?\n\n1. Executive AI/data strategy\n2. Life sciences / genomics\n3. Technical architecture\n4. Partner enablement\n5. HPC / research computing\n6. General DDN overview','Choose the conversation type.');
      ddn.useCase=(v==='life'||v==='general')?'life_sciences_genomics':v==='hpc'?'hpc_research_computing':'ai_genai_infrastructure'; ddn.product='recommend'; ddn.audience ??='executive'; ddn.depth ??='standard'; ddn.stage='audience';
      return ask('Who is the audience?\n\n1. Executive / CIO / CTO\n2. Infrastructure leadership\n3. AI / data platform team\n4. Research / scientific computing team\n5. Storage / HPC technical team\n6. Partner sales / SE enablement','Choose the audience.');
    }
    case 'audience': {
      const v=parse(userText,{executive:'1|executive|cio|cto',infrastructure:'2|infrastructure leadership',ai_data_platform:'3|ai / data platform team|ai data platform',research_scientific:'4|research|scientific computing',storage_hpc:'5|storage|hpc technical',partner_enablement:'6|partner sales|se enablement'});
      if(!v)return ask('Who is the audience?\n\n1. Executive / CIO / CTO\n2. Infrastructure leadership\n3. AI / data platform team\n4. Research / scientific computing team\n5. Storage / HPC technical team\n6. Partner sales / SE enablement','Choose the audience.');
      ddn.audience=v as DdnAudience; ddn.stage='customer';
      return ask('Customer or account name?\nType the name, or choose 1 to skip.','Add the customer name, or choose skip.');
    }
    case 'customer':
      if(!userText) return ask('Customer or account name?\nType the name, or choose 1 to skip.','Add the customer name, or choose skip.');
      ddn.customer = (n(userText)==='1'||n(userText)==='skip')?'Skipped':userText; ddn.stage='depth';
      return ask('How deep should this deck go?\n\n1. Short executive deck\n2. Standard customer meeting\n3. Technical detail included','Choose the deck depth.');
    case 'depth': {
      const v=parse(userText,{short_exec:'1|short executive deck',standard:'2|standard customer meeting',technical:'3|technical detail included'});
      if(!v)return ask('How deep should this deck go?\n\n1. Short executive deck\n2. Standard customer meeting\n3. Technical detail included','Choose the deck depth.');
      ddn.depth=v as DdnDepth; ddn.stage='notes';
      return ask('Anything specific to include?\nType notes, or choose 1 to skip.','Add optional notes, or choose skip.');
    }
    case 'notes':
      if(!userText) return ask('Anything specific to include?\nType notes, or choose 1 to skip.','Add optional notes, or choose skip.');
      ddn.notes=(n(userText)==='1'||n(userText)==='skip')?'Skipped':userText; ddn.stage='review';
      break;
    case 'edit': {
      const c=n(userText); if(c==='1'){ddn.stage='starting';ddn.startingPoint=undefined;} else if(c==='2'){ddn.stage='use_case';ddn.useCase=undefined;} else if(c==='3'){ddn.stage='product';ddn.product=undefined;} else if(c==='4'){ddn.stage='audience';ddn.audience=undefined;} else if(c==='5'){ddn.stage='customer';ddn.customer=undefined;} else if(c==='6'){ddn.stage='depth';ddn.depth=undefined;} else if(c==='7'){ddn.stage='notes';ddn.notes=undefined;}
      return ask('Updated. Let’s continue your DDN deck setup.','Updated. Let’s continue.');
    }
    default:
      break;
  }

  const focus = ddn.useCase==='life_sciences_genomics' ? 'Infinia + Data Intelligence Platform' : (ddn.product && ddn.product!=='recommend' ? labelMap.product[ddn.product] : recommendFocus(ddn.useCase!));
  const outline = buildDdnOutline(ddn.useCase!, ddn.depth!, now());
  const review=['Here’s the DDN deck I’ll create:','',`1. Starting point: ${labelMap.startingPoint[ddn.startingPoint!]}`,`2. Use case: ${labelMap.useCase[ddn.useCase!]}`,`3. Customer: ${ddn.customer}`,`4. Audience: ${labelMap.audience[ddn.audience!]}`,`5. Recommended DDN focus: ${focus}`,`6. Depth: ${labelMap.depth[ddn.depth!]}`,`7. Estimated length: ${DEPTH_SLIDE_COUNT[ddn.depth!]} slides`,'','Deck flow:',...outline.slides.map((s)=>`${s.slide_number}. ${s.title}`),'','Next:','1. This is correct — create the PowerPoint','2. Edit the deck setup'].join('\n');
  if (ddn.stage==='review' && !['1','2'].includes(n(userText))) return ask(review,'If this looks correct, choose 1 to create the PowerPoint. To edit, choose 2.');
  if (n(userText)==='2'){ddn.stage='edit'; return ask('What do you want to edit?\n\n1. Starting point\n2. Use case\n3. Product / solution focus\n4. Audience\n5. Customer/account name\n6. Deck depth\n7. Optional notes','Choose what you want to edit.');}

  state.outline = outline; state.outline.status='outline_approved'; state.deckBrief.topic=`DDN presentation - ${labelMap.useCase[ddn.useCase!]}`; state.deckBrief.audience=labelMap.audience[ddn.audience!]; state.deckBrief.use_case=labelMap.useCase[ddn.useCase!]; state.deckBrief.slide_count=DEPTH_SLIDE_COUNT[ddn.depth!]; state.deckBrief.technical_depth=ddn.depth==='technical'?'high':ddn.depth==='short_exec'?'low':'medium'; state.deckBrief.deck_type=ddn.audience==='partner_enablement'?'partner_enablement':'customer_executive_briefing'; state.deckBrief.tone='technical_but_executive_readable'; state.deckBrief.customer_context=ddn.customer==='Skipped'?'':ddn.customer; state.deckBrief.user_notes=ddn.notes==='Skipped'?'':ddn.notes; state.deckBrief.status='outline_approved';

  const result = await generatePptxRuntime(session, state);
  state.generatedPresentation = { status:'generated', format:'pptx', file_name:result.fileName, download_url:result.downloadUrl, generated_at:result.generatedAt, theme_id:result.themeId, ...(result.filePath?{file_path:result.filePath}:{})};
  const history = toGeneratedDeckHistoryItem(state.generatedPresentation, state.deckBrief, state.outline); if (history) state.generatedDeckHistory = appendGeneratedDeckHistory(state.generatedDeckHistory, history);
  state.step = 'presentation_generated';
  return ask(`Your PowerPoint is ready: ${result.downloadUrl}`);
};
