import {
  CREATE_PRESENTATIONS_TECHNICAL_DEPTH,
  CREATE_PRESENTATIONS_TONES,
  type CreatePresentationsDeckBrief,
  type CreatePresentationsModeEvent,
  type CreatePresentationsOptionalField,
} from '../../../../shared/contracts/createPresentationsMode';
import type { AskChappySession } from '../sessions/sessionStore';
import { appendGeneratedDeckHistory, toGeneratedDeckHistoryItem } from './createPresentationsDeckHistory';
import { generateOutlineFromBrief, renderOutlineReview, validateOutlineForApproval } from './createPresentationsOutlineFlow';

export type GuidedAssistantResponse = { text: string; spokenText?: string };
const answer = (text: string, spokenText?: string): GuidedAssistantResponse => ({ text, spokenText });
const now = () => new Date().toISOString();
const nextEvent = (event: Omit<CreatePresentationsModeEvent, 'id' | 'ts'>): CreatePresentationsModeEvent => ({ id: `evt_${crypto.randomUUID()}`, ts: now(), ...event });
const normalizeAnswer = (input: string) => input.trim().toLowerCase().replace(/[.!?]+$/g, '').replace(/\s+/g, ' ');
const NUMBER_WORDS: Record<string, number> = {
  one: 1,
  two: 2,
  three: 3,
  four: 4,
  five: 5,
  six: 6,
  seven: 7,
  eight: 8,
  nine: 9,
  ten: 10,
  eleven: 11,
  twelve: 12,
  thirteen: 13,
  fourteen: 14,
  fifteen: 15,
  sixteen: 16,
  seventeen: 17,
  eighteen: 18,
  nineteen: 19,
  twenty: 20,
  twentyone: 21,
  twentytwo: 22,
  twentythree: 23,
  twentyfour: 24,
  twentyfive: 25,
  twentysix: 26,
  twentyseven: 27,
  twentyeight: 28,
  twentynine: 29,
  thirty: 30,
};
const parseNumberChoice = (input: string): number | undefined => {
  const n = normalizeAnswer(input);
  const d = n.match(/(?:option|choice|number)?\s*(\d{1,2})$/)?.[1];
  if (d) return Number.parseInt(d,10);
  const compact = n.replace(/[-\s]/g, '');
  if (NUMBER_WORDS[compact]) return NUMBER_WORDS[compact];
  const wm = n.match(/(?:option|choice|number)\s+([a-z\s-]+)/)?.[1]?.replace(/[-\s]/g,'');
  return wm ? NUMBER_WORDS[wm] : undefined;
};
const parseSlideCount = (input: string): number | undefined => {
  const n = parseNumberChoice(input) ?? Number.parseInt(normalizeAnswer(input).match(/\d+/)?.[0] ?? '', 10);
  return Number.isInteger(n) && n >= 3 && n <= 30 ? n : undefined;
};
const isSkipInput = (input: string) => new Set(['1','skip','none','no','n/a','not applicable','leave blank','blank','no thanks']).has(normalizeAnswer(input));
const isApprovalInput = (input: string) => /^(approve|approved|approve this brief|looks good|looks good to me|go ahead|yes|yes approve|no changes|no changes needed|good|continue)$/.test(normalizeAnswer(input));
const isRevisionInput = (input: string) => /(revise|change|edit|update|fix|change something)/.test(normalizeAnswer(input));
const deckTypeMenu = 'Choose one of the options below:\n1. Executive briefing\n2. Technical deep dive\n3. Partner enablement\n4. Internal training\n5. Architecture review\n6. Workshop\n7. Roadmap\n8. Proposal\n9. Custom';
const toneMenu = 'Choose a tone:\n1. Executive\n2. Consultative\n3. Technical\n4. Technical but executive readable\n5. Sales\n6. Training\n7. Concise\n8. Custom';
const depthMenu = 'Choose technical depth:\n1. Low\n2. Medium\n3. High\n4. Mixed';
const speakerNotesMenu = 'Include speaker notes?\n1. Yes\n2. No';
const deckTypeFrom = (input: string): CreatePresentationsDeckBrief['deck_type'] | undefined => {
  const n = parseNumberChoice(input); const x = normalizeAnswer(input);
  if (n===1 || /executive/.test(x)) return 'customer_executive_briefing'; if (n===2 || /technical/.test(x)) return 'customer_technical_deep_dive'; if (n===3 || /partner/.test(x)) return 'partner_enablement'; if (n===4 || /training/.test(x)) return 'internal_training'; if (n===5 || /architecture/.test(x)) return 'architecture_review'; if (n===6 || /workshop/.test(x)) return 'workshop'; if (n===7 || /roadmap/.test(x)) return 'roadmap'; if (n===8 || /proposal/.test(x)) return 'proposal'; if (n===9 || /custom/.test(x)) return 'custom';
};
const toneFrom = (input: string): (typeof CREATE_PRESENTATIONS_TONES)[number] | undefined => { const n=parseNumberChoice(input); const x=normalizeAnswer(input); if(n===1||x==='executive')return'executive'; if(n===2||x==='consultative')return'consultative'; if(n===3||x==='technical')return'technical'; if(n===4||x.includes('technical')&&x.includes('executive'))return'technical_but_executive_readable'; if(n===5||x==='sales')return'sales'; if(n===6||x==='training')return'training'; if(n===7||x==='concise')return'concise'; if(n===8||x==='custom')return'custom'; };
const depthFrom = (input: string): (typeof CREATE_PRESENTATIONS_TECHNICAL_DEPTH)[number] | undefined => { const n=parseNumberChoice(input); const x=normalizeAnswer(input); if(n===1||/low|light/.test(x))return'low'; if(n===2||/medium|moderate/.test(x))return'medium'; if(n===3||/high|deep/.test(x))return'high'; if(n===4||x==='mixed')return'mixed'; };
const speakerNotesFrom = (input:string): boolean|undefined => { const n=parseNumberChoice(input); const x=normalizeAnswer(input); if(n===1||x==='yes'||x==='y') return true; if(n===2||x==='no'||x==='n') return false; };
const labels: Record<string, string> = {
  customer_executive_briefing: 'Executive briefing',
  customer_technical_deep_dive: 'Technical deep dive',
  partner_enablement: 'Partner enablement',
  internal_training: 'Internal training',
  architecture_review: 'Architecture review',
  workshop: 'Workshop',
  roadmap: 'Roadmap',
  proposal: 'Proposal',
  custom: 'Custom',
  technical_but_executive_readable: 'Technical but executive readable',
  executive: 'Executive',
  consultative: 'Consultative',
  technical: 'Technical',
  sales: 'Sales',
  training: 'Training',
  concise: 'Concise',
  low: 'Low',
  medium: 'Medium',
  high: 'High',
  mixed: 'Mixed',
};

export const handleCreatePresentationsTurn = async (session: AskChappySession): Promise<GuidedAssistantResponse> => { /* shortened */
  const state = session.metadata.askchappy.create_presentations_state!; const brief=state.deckBrief; state.generatedDeckHistory ??= [];
  const userText=[...session.transcript].reverse().find((m)=>m.role==='user')?.text?.trim()??''; const n=normalizeAnswer(userText);
  const ask=(text:string,spoken?:string)=>{ state.events.push(nextEvent({actor:'assistant',step:state.step,kind:'question_asked',text})); return answer(text,spoken); };
  const markSkipped=(f:CreatePresentationsOptionalField)=>{ if(!state.skippedFields.includes(f)) state.skippedFields.push(f); };
  if (state.step==='intro') state.step='collecting_brief';
  if (state.step==='brief_review') {
    if (isApprovalInput(userText)) { brief.status='brief_approved'; state.step='brief_approved'; state.outline=generateOutlineFromBrief(brief, now()); brief.status='outline_review'; state.step='outline_review'; return ask(`Great — I approved the brief and generated the outline. Review it below.\n\n${renderOutlineReview(state.outline)}`,'Great — I generated the outline. Review it below.'); }
    if (n==='user_notes') return ask('What should user notes be? Type the new value, or choose 1 to skip.');
    if (isRevisionInput(userText)) return ask('What do you want to revise?\n1. Deck type\n2. Topic\n3. Audience\n4. Slide count\n5. Tone\n6. Technical depth\n7. Must-include sections\n8. Constraints\n9. Required messaging\n10. User notes\n11. Speaker notes','Choose what you want to revise.');
  }
  if (state.step==='outline_review') {
    if (/(approve and create|approve.*powerpoint|approve.*pptx)/i.test(userText)) { state.outline.status='outline_approved'; brief.status='outline_approved'; state.step='outline_approved'; }
    else if (/(approve|approve outline|yes|looks good|no changes|continue)/i.test(n)) { state.outline.status='outline_approved'; brief.status='outline_approved'; state.step='outline_approved'; return ask('Outline approved. Next:\n1. Create PowerPoint\n2. Revise outline','Outline approved. Choose the next step below.'); }
  }
  if (state.step==='outline_approved' || state.step==='presentation_generated') {
    if (!(parseNumberChoice(userText)===1 || /(create powerpoint|generate presentation|generate pptx|export pptx)/i.test(userText))) return ask('Outline approved. Next:\n1. Create PowerPoint\n2. Revise outline','Outline approved. Choose the next step below.');
    const { generatePptxFromApprovedOutline } = await import('./createPresentationsPptxGenerator'); const result = await generatePptxFromApprovedOutline(session.session_id, brief, state.outline);
    state.generatedPresentation={status:'generated',format:'pptx',file_name:result.fileName,file_path:result.filePath,download_url:result.downloadUrl,generated_at:result.generatedAt,theme_id:result.themeId};
    const h=toGeneratedDeckHistoryItem(state.generatedPresentation,brief,state.outline); if(h) state.generatedDeckHistory=appendGeneratedDeckHistory(state.generatedDeckHistory,h); state.step='presentation_generated';
    return ask(`Your PowerPoint is ready: ${result.downloadUrl}`);
  }
  if (!brief.deck_type) { const m=deckTypeFrom(userText); if(!m) return ask(deckTypeMenu,'Choose one of the options below.'); brief.deck_type=m; }
  else if (!brief.topic) brief.topic=userText;
  else if (!brief.audience) brief.audience=userText;
  else if (!brief.customer_context && !state.skippedFields.includes('customer_context')) { if(isSkipInput(userText)) markSkipped('customer_context'); else if (n==='yes') return ask('What should I include? Type it, or choose 1 to skip.'); else brief.customer_context=userText; }
  else if (!brief.industry && !state.skippedFields.includes('industry')) { if(isSkipInput(userText)) markSkipped('industry'); else brief.industry=userText; }
  else if (!brief.use_case && !state.skippedFields.includes('use_case')) { if(isSkipInput(userText)) markSkipped('use_case'); else brief.use_case=userText; }
  else if (typeof brief.slide_count !=='number') { const sc=parseSlideCount(userText); if(!sc) return ask('Slide count must be an integer between 3 and 30.'); brief.slide_count=sc; }
  else if (!brief.tone) { const t=toneFrom(userText); if(!t) return ask(toneMenu,'Choose a tone from the list below.'); brief.tone=t; }
  else if (!brief.technical_depth) { const d=depthFrom(userText); if(!d) return ask(depthMenu,'Choose the technical depth below.'); brief.technical_depth=d; }
  else if (!brief.must_include && !state.skippedFields.includes('must_include')) { if(isSkipInput(userText)) markSkipped('must_include'); else if(/scared|afraid|nervous/i.test(userText)) return ask('No problem — we can keep this simple. Type a comma-separated list, or choose 1 to skip.'); else brief.must_include=userText.split(',').map(i=>i.trim()).filter(Boolean); }
  else if (!brief.constraints && !state.skippedFields.includes('constraints')) { if(isSkipInput(userText)) markSkipped('constraints'); else brief.constraints=userText.split(',').map(i=>i.trim()).filter(Boolean); }
  else if (!brief.required_messaging && !state.skippedFields.includes('required_messaging')) { if(isSkipInput(userText)) markSkipped('required_messaging'); else brief.required_messaging=userText.split(',').map(i=>i.trim()).filter(Boolean); }
  else if (!brief.user_notes && !state.skippedFields.includes('user_notes')) { if(isSkipInput(userText)) markSkipped('user_notes'); else brief.user_notes=userText; }
  else if (typeof brief.output.speaker_notes !== 'boolean') { const s=speakerNotesFrom(userText); if(typeof s!=='boolean') return ask(speakerNotesMenu,'Choose yes or no below.'); brief.output.speaker_notes=s; }

  const render=(v:unknown)=> Array.isArray(v)?(v.length?v.join(', '):'Skipped'):(v??'Skipped');
  const next = !brief.topic?'What is the presentation topic?':!brief.audience?'Who is the audience?':!brief.customer_context&&!state.skippedFields.includes('customer_context')?'Customer/company context? Type it, or choose 1 to skip.':!brief.industry&&!state.skippedFields.includes('industry')?'Industry? Type it, or choose 1 to skip.':!brief.use_case&&!state.skippedFields.includes('use_case')?'Primary use case? Type it, or choose 1 to skip.':typeof brief.slide_count!=='number'?'How many slides do you want (3–30)?':!brief.tone?toneMenu:!brief.technical_depth?depthMenu:!brief.must_include&&!state.skippedFields.includes('must_include')?'Must-include sections? Type a comma-separated list, or choose 1 to skip.':!brief.constraints&&!state.skippedFields.includes('constraints')?'Constraints? Type a comma-separated list, or choose 1 to skip.':!brief.required_messaging&&!state.skippedFields.includes('required_messaging')?'Required messaging points? Type a comma-separated list, or choose 1 to skip.':!brief.user_notes&&!state.skippedFields.includes('user_notes')?'Extra user notes? Type notes, or choose 1 to skip.':typeof brief.output.speaker_notes!=='boolean'?speakerNotesMenu:'';
  if (next) return ask(next, next===toneMenu?'Choose a tone from the list below.':next===depthMenu?'Choose the technical depth below.':next===speakerNotesMenu?'Choose yes or no below.':'');
  state.step='brief_review'; brief.status='brief_review';
  return ask(`Here’s the brief I heard:\n1. Deck type: ${labels[brief.deck_type!]}\n2. Topic: ${brief.topic}\n3. Audience: ${brief.audience}\n4. Customer context: ${render(brief.customer_context)}\n5. Industry: ${render(brief.industry)}\n6. Primary use case: ${render(brief.use_case)}\n7. Slide count: ${brief.slide_count}\n8. Tone: ${labels[brief.tone!]}\n9. Technical depth: ${labels[brief.technical_depth!]}\n10. Must-include sections: ${render(brief.must_include)}\n11. Constraints: ${render(brief.constraints)}\n12. Required messaging: ${render(brief.required_messaging)}\n13. User notes: ${render(brief.user_notes)}\n14. Speaker notes: ${brief.output.speaker_notes ? 'Yes' : 'No'}\n\nNext:\n1. Approve and generate outline\n2. Revise brief`,'Here’s the brief I heard. Choose approve or revise below.');
};
