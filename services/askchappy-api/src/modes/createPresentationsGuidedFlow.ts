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
const deckTypeMenu = [
  'Choose one of the options below:',
  '1. Executive briefing',
  '2. Technical deep dive',
  '3. Partner enablement',
  '4. Internal training',
  '5. Architecture review',
  '6. Workshop',
  '7. Roadmap',
  '8. Proposal',
  '9. Custom',
].join('\n');

const toneMenu = [
  'Choose a tone:',
  '1. Executive',
  '2. Consultative',
  '3. Technical',
  '4. Technical but executive readable',
  '5. Sales',
  '6. Training',
  '7. Concise',
  '8. Custom',
].join('\n');

const depthMenu = [
  'Choose technical depth:',
  '1. Low',
  '2. Medium',
  '3. High',
  '4. Mixed',
].join('\n');

const speakerNotesMenu = [
  'Include speaker notes?',
  '1. Yes',
  '2. No',
].join('\n');

const revisionMenuText = [
  'What do you want to revise?',
  '1. Deck type',
  '2. Topic',
  '3. Audience',
  '4. Slide count',
  '5. Tone',
  '6. Technical depth',
  '7. Must-include sections',
  '8. Constraints',
  '9. Required messaging',
  '10. User notes',
  '11. Speaker notes',
].join('\n');

const revisionFieldByChoice: Record<number, string> = { 1:'deck_type',2:'topic',3:'audience',4:'slide_count',5:'tone',6:'technical_depth',7:'must_include',8:'constraints',9:'required_messaging',10:'user_notes',11:'speaker_notes' };
const applyBriefRevisionFromText = (brief: CreatePresentationsDeckBrief, input: string): string | undefined => {
  const n = normalizeAnswer(input);
  const deckType = deckTypeFrom(input);
  if (/(^|\s)(set|change|update).*deck type/.test(n) && deckType) { brief.deck_type = deckType; return 'deck_type'; }
  if (/(^|\s)(set|change|update).*(topic)/.test(n)) { const v=input.replace(/.*topic\s*(to|as)?\s*/i,'').trim(); if (v) { brief.topic=v; return 'topic'; } }
  if (/(^|\s)(set|change|update).*(audience)/.test(n)) { const v=input.replace(/.*audience\s*(to|as)?\s*/i,'').trim(); if (v) { brief.audience=v; return 'audience'; } }
  const slideCount = parseSlideCount(input);
  if (/(^|\s)(set|change|update).*(slide count|slides)/.test(n) && slideCount) { brief.slide_count = slideCount; return 'slide_count'; }
  const tone = toneFrom(input);
  if (/(^|\s)(set|change|update).*(tone)/.test(n) && tone) { brief.tone=tone; return 'tone'; }
  const depth = depthFrom(input);
  if (/(^|\s)(set|change|update).*(technical depth|depth)/.test(n) && depth) { brief.technical_depth=depth; return 'technical_depth'; }
  if (/(^|\s)(set|change|update).*(must-include|must include)/.test(n)) { const v=input.replace(/.*(must-include|must include)( sections?)?\s*(to|as)?\s*/i,'').trim(); brief.must_include=v?v.split(',').map(i=>i.trim()).filter(Boolean):[]; return 'must_include'; }
  if (/(^|\s)(set|change|update).*(constraints?)/.test(n)) { const v=input.replace(/.*constraints?\s*(to|as)?\s*/i,'').trim(); brief.constraints=v?v.split(',').map(i=>i.trim()).filter(Boolean):[]; return 'constraints'; }
  if (/(^|\s)(set|change|update).*(required messaging|messaging)/.test(n)) { const v=input.replace(/.*(required messaging|messaging)( points?)?\s*(to|as)?\s*/i,'').trim(); brief.required_messaging=v?v.split(',').map(i=>i.trim()).filter(Boolean):[]; return 'required_messaging'; }
  if (/(^|\s)(set|change|update).*(user notes)/.test(n)) { const v=input.replace(/.*user notes\s*(to|as)?\s*/i,'').trim(); brief.user_notes=v; return 'user_notes'; }
  const sn = speakerNotesFrom(input);
  if (/(^|\s)(set|change|update).*(speaker notes)/.test(n) && typeof sn === 'boolean') { brief.output.speaker_notes=sn; return 'speaker_notes'; }
};

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
  const render=(v:unknown)=> Array.isArray(v)?(v.length?v.join(', '):'Skipped'):(v??'Skipped');
  const renderBriefReview = () => `Here’s the brief I heard:\n1. Deck type: ${labels[brief.deck_type!]}\n2. Topic: ${brief.topic}\n3. Audience: ${brief.audience}\n4. Customer context: ${render(brief.customer_context)}\n5. Industry: ${render(brief.industry)}\n6. Primary use case: ${render(brief.use_case)}\n7. Slide count: ${brief.slide_count}\n8. Tone: ${labels[brief.tone!]}\n9. Technical depth: ${labels[brief.technical_depth!]}\n10. Must-include sections: ${render(brief.must_include)}\n11. Constraints: ${render(brief.constraints)}\n12. Required messaging: ${render(brief.required_messaging)}\n13. User notes: ${render(brief.user_notes)}\n14. Speaker notes: ${brief.output.speaker_notes ? 'Yes' : 'No'}\n\nNext:\n1. Approve and generate outline\n2. Revise brief`;
  if (state.step==='intro') state.step='collecting_brief';
  if (state.step==='brief_review') {
    if (isApprovalInput(userText)) { brief.status='brief_approved'; state.step='brief_approved'; state.outline=generateOutlineFromBrief(brief, now()); brief.status='outline_review'; state.step='outline_review'; return ask(`Great — I approved the brief and generated the outline. Review it below.\n\n${renderOutlineReview(state.outline)}`,'Great — I generated the outline. Review it below.'); }
    if ((state as any).pendingBriefRevisionField) {
      const field = (state as any).pendingBriefRevisionField as string;
      const mapping: Record<string, string> = { deck_type:'Deck type', topic:'Topic', audience:'Audience', slide_count:'Slide count', tone:'Tone', technical_depth:'Technical depth', must_include:'Must-include sections', constraints:'Constraints', required_messaging:'Required messaging', user_notes:'User notes', speaker_notes:'Speaker notes' };
      const parsed = applyBriefRevisionFromText(brief, `set ${mapping[field] ?? field} to ${userText}`);
      if (!parsed) return ask(`I couldn't update that yet. Please provide a valid value for ${mapping[field] ?? field}.`);
      state.events.push(nextEvent({ actor:'assistant', step:state.step, kind:'brief_updated', field: parsed as any } as any));
      (state as any).pendingBriefRevisionField = undefined;
      return ask(renderBriefReview(),'Here’s the updated brief.');
    }
    if (/approve with changes/.test(n)) return ask(revisionMenuText,'Choose what you want to revise.');
    const revised = applyBriefRevisionFromText(brief, userText);
    if (revised) { state.events.push(nextEvent({ actor:'assistant', step:state.step, kind:'brief_updated', field: revised as any } as any)); (state as any).pendingBriefRevisionField = undefined; return ask(renderBriefReview(),'Here’s the updated brief.'); }
    const choice = parseNumberChoice(userText);
    if (choice && revisionFieldByChoice[choice]) {
      (state as any).pendingBriefRevisionField = revisionFieldByChoice[choice];
      const promptByField: Record<string,string> = { deck_type:deckTypeMenu, topic:'What should the topic be?', audience:'Who is the audience?', slide_count:'What should slide count be? (3–30)', tone:toneMenu, technical_depth:depthMenu, must_include:'What should must-include sections be? (comma-separated)', constraints:'What should constraints be? (comma-separated)', required_messaging:'What should required messaging be? (comma-separated)', user_notes:'What should user notes be?', speaker_notes:speakerNotesMenu };
      return ask(promptByField[revisionFieldByChoice[choice]],'Tell me the updated value.');
    }
    if (n==='user_notes') return ask('What should user notes be? Type the new value, or choose 1 to skip.');
    if (isRevisionInput(userText)) return ask(revisionMenuText,'Choose what you want to revise.');
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

  const next = !brief.topic?'What is the presentation topic?':!brief.audience?'Who is the audience?':!brief.customer_context&&!state.skippedFields.includes('customer_context')?'Customer/company context? Type it, or choose 1 to skip.':!brief.industry&&!state.skippedFields.includes('industry')?'Industry? Type it, or choose 1 to skip.':!brief.use_case&&!state.skippedFields.includes('use_case')?'Primary use case? Type it, or choose 1 to skip.':typeof brief.slide_count!=='number'?'How many slides do you want (3–30)?':!brief.tone?toneMenu:!brief.technical_depth?depthMenu:!brief.must_include&&!state.skippedFields.includes('must_include')?'Must-include sections? Type a comma-separated list, or choose 1 to skip.':!brief.constraints&&!state.skippedFields.includes('constraints')?'Constraints? Type a comma-separated list, or choose 1 to skip.':!brief.required_messaging&&!state.skippedFields.includes('required_messaging')?'Required messaging points? Type a comma-separated list, or choose 1 to skip.':!brief.user_notes&&!state.skippedFields.includes('user_notes')?'Extra user notes? Type notes, or choose 1 to skip.':typeof brief.output.speaker_notes!=='boolean'?speakerNotesMenu:'';
  if (next) return ask(next, next===toneMenu?'Choose a tone from the list below.':next===depthMenu?'Choose the technical depth below.':next===speakerNotesMenu?'Choose yes or no below.':'');
  state.step='brief_review'; brief.status='brief_review';
  return ask(renderBriefReview(),'Here’s the brief I heard. Choose approve or revise below.');
};
