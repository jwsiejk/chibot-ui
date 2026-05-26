import fs from 'node:fs/promises';
import path from 'node:path';
import type { CreatePresentationsDeckBrief, CreatePresentationsOutlineState } from '../../../../shared/contracts/createPresentationsMode';

const OUTPUT_DIR = path.resolve(process.cwd(), 'generated/presentations');
const esc = (s: string) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
const sanitize = (value: string) => value.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/(^-|-$)/g, '').slice(0, 60) || 'presentation';
export const getPresentationOutputDir = () => OUTPUT_DIR;
export const resolvePresentationPathFromFileName = (fileName: string) => { if (!/^[a-z0-9][a-z0-9-]*\.pptx$/i.test(fileName)) throw new Error('Invalid presentation file name.'); const p = path.resolve(OUTPUT_DIR, fileName); if (!p.startsWith(OUTPUT_DIR)) throw new Error('Invalid presentation path.'); return p; };

const slideXml = (title:string,obj:string,points:string[]) => `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr/><p:sp><p:nvSpPr><p:cNvPr id="2" name="Title"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr><p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:t>${esc(title)}</a:t></a:r></a:p></p:txBody></p:sp><p:sp><p:nvSpPr><p:cNvPr id="3" name="Body"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr><p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:t>Objective: ${esc(obj)}</a:t></a:r></a:p>${points.map(p=>`<a:p><a:pPr lvl="0"><a:buChar char="•"/></a:pPr><a:r><a:t>${esc(p)}</a:t></a:r></a:p>`).join('')}</p:txBody></p:sp></p:spTree></p:cSld></p:sld>`;

export const generatePptxFromApprovedOutline = async (sessionId: string, brief: CreatePresentationsDeckBrief, outline: CreatePresentationsOutlineState) => {
  await fs.mkdir(OUTPUT_DIR, { recursive: true });
  const fileName = `${sanitize(sessionId)}-${sanitize(brief.topic ?? 'deck')}-${Date.now()}.pptx`;
  const filePath = resolvePresentationPathFromFileName(fileName);
  const tmp = filePath.replace(/\.pptx$/, '');
  await fs.mkdir(`${tmp}/ppt/slides/_rels`, { recursive: true });
  await fs.mkdir(`${tmp}/ppt/_rels`, { recursive: true });
  await fs.mkdir(`${tmp}/_rels`, { recursive: true });
  await fs.mkdir(`${tmp}/docProps`, { recursive: true });
  await fs.writeFile(`${tmp}/[Content_Types].xml`, `<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>${outline.slides.map((_,i)=>`<Override PartName="/ppt/slides/slide${i+1}.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>`).join('')}</Types>`);
  await fs.writeFile(`${tmp}/_rels/.rels`, `<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="ppt/presentation.xml"/></Relationships>`);
  await fs.writeFile(`${tmp}/ppt/presentation.xml`, `<?xml version="1.0" encoding="UTF-8"?><p:presentation xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:sldIdLst>${outline.slides.map((_,i)=>`<p:sldId id="${256+i}" r:id="rId${i+1}"/>`).join('')}</p:sldIdLst></p:presentation>`);
  await fs.writeFile(`${tmp}/ppt/_rels/presentation.xml.rels`, `<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">${outline.slides.map((_,i)=>`<Relationship Id="rId${i+1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide${i+1}.xml"/>`).join('')}</Relationships>`);
  await Promise.all(outline.slides.map((s,i)=>fs.writeFile(`${tmp}/ppt/slides/slide${i+1}.xml`, slideXml(s.title,s.objective,s.key_points))));
  await Promise.all(outline.slides.map((_,i)=>fs.writeFile(`${tmp}/ppt/slides/_rels/slide${i+1}.xml.rels`, `<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"></Relationships>`)));
  await fs.writeFile(`${tmp}/docProps/core.xml`, `<?xml version="1.0" encoding="UTF-8"?><cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"><dc:title xmlns:dc="http://purl.org/dc/elements/1.1/">${esc(brief.topic ?? 'Presentation')}</dc:title></cp:coreProperties>`);
  await fs.writeFile(`${tmp}/docProps/app.xml`, `<?xml version="1.0" encoding="UTF-8"?><Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"></Properties>`);
  await fs.writeFile(`${tmp}/ppt/presProps.xml`, `<?xml version="1.0" encoding="UTF-8"?><p:presentationPr xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"/>`);
  await fs.writeFile(`${tmp}/ppt/viewProps.xml`, `<?xml version="1.0" encoding="UTF-8"?><p:viewPr xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"/>`);
  await fs.writeFile(`${tmp}/ppt/tableStyles.xml`, `<?xml version="1.0" encoding="UTF-8"?><a:tblStyleLst xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"/>`);
  await fs.writeFile(`${tmp}/ppt/theme1.xml`, '<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" name="Default"></a:theme>');
  await fs.writeFile(`${tmp}/ppt/slideMasters.xml`, '');
  await fs.writeFile(`${tmp}/ppt/slideLayouts.xml`, '');
  await fs.writeFile(`${tmp}/ppt/notesMasters.xml`, '');
  await fs.writeFile(`${tmp}/ppt/handoutMasters.xml`, '');
  const { execFile } = await import('node:child_process');
  await new Promise<void>((res, rej) => execFile('zip', ['-r', filePath, '.'], { cwd: tmp }, (e) => e ? rej(e) : res()));
  await fs.rm(tmp, { recursive: true, force: true });
  return { fileName, filePath, downloadUrl: `/api/presentations/${fileName}`, generatedAt: new Date().toISOString() };
};
