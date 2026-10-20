// A new synthetic session with the real server clock, selected model and browser.
// Run with normal AI/Report Workers. No clock compression or model mocks.
import {createRequire} from 'node:module';
import fs from 'node:fs/promises';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
const compressed=process.env.DAYZERO_VERIFY_COMPRESSED==='1';
const run=promisify(execFile);
const evidencePrefix='docs/evidence/revision-'+(compressed?'compressed':'full120');
const require=createRequire(new URL('../apps/web/package.json',import.meta.url));
const {chromium,request}=require('playwright');
const api=await request.newContext({baseURL:'http://127.0.0.1:8000'});
const alias=(compressed?'개정 압축 합성 검증 ':'개정 실제 120분 합성 검증 ')+Date.now();
let phase='setup',action='setup',sid,start,wallStart,auth,admin,browser,page;
const evidence={synthetic_fixture:true,actual_model:true,compressed_clock:compressed,completed:false,steps:[]};
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
async function call(path,method='GET',data,headers=auth){const r=await api.fetch(path,{method,data,headers});if(!r.ok())throw Error('HTTP '+r.status()+' '+path);return r.json()}
async function checkpoint(label){const w=await call('/sessions/'+sid);evidence.steps.push({label,at:new Date().toISOString(),elapsed_seconds:Date.now()/1000-start,records:w.records.length,tasks:w.tasks.length});console.log(label,Math.round(Date.now()/1000-start));await fs.writeFile(evidencePrefix+'-progress.json',JSON.stringify(evidence,null,2));return w}
async function at(seconds,label){phase=label;if(compressed){await run('services/dayzero/.venv/bin/python',['scripts/advance_fixture_clock.py',sid,String(seconds)]);start=(await call('/sessions/'+sid)).started}else while(Date.now()/1000-start<seconds)await sleep(Math.min(10000,(seconds-(Date.now()/1000-start))*1000));return checkpoint(label)}
async function openApp(name){action='open '+name;await page.getByRole('button',{name,exact:true}).click()}
async function send(agent,text,channel='team'){
 await openApp(agent==='chat'?'AI 채팅':'메신저');
 if(agent!=='chat'){action='select conversation '+channel;await page.getByRole('button',{name:channel==='dm'?'이도현 1:1':'# 팀 채널',exact:true}).click();if(channel==='team'){action='select recipient '+agent;await page.getByLabel('수신자',{exact:true}).selectOption(agent)}}
 const label=agent==='chat'?'AI 메시지':channel==='dm'?'동료 메시지':'팀 메시지';
 action='compose '+agent;await page.getByLabel(label,{exact:true}).fill(text);
 const response=page.waitForResponse(r=>r.url().endsWith('/messages')&&r.request().method()==='POST');
 action='send '+agent;await page.getByRole('button',{name:'보내기',exact:true}).click();const sent=await response;if(!sent.ok())throw Error('message rejected '+sent.status());return(await sent.json()).id;
}
async function answer(mid){const limit=Date.now()+460000;while(Date.now()<limit){const w=await call('/sessions/'+sid);const reply=w.records.find(r=>r.data.reply_to===mid);if(reply)return reply;const job=w.jobs.find(j=>j.input_id===mid&&j.status==='failed');if(job)throw Error('AI failed '+job.error);await sleep(1000)}throw Error('answer timeout')}
async function edit(title,text){await openApp('업무·제출');await openApp('공유 기획안');const target=page.getByRole('textbox',{name:title,exact:true});await target.fill(text);await target.blur();const limit=Date.now()+10000;while(Date.now()<limit){if((await call('/sessions/'+sid)).parts.find(p=>p.title===title).body===text)return;await sleep(300)}throw Error('document save failed')}
async function assign(title,partNumber,note){await openApp('업무·제출');await openApp('태스크 보드');await page.getByLabel('태스크 제목',{exact:true}).fill(title);await page.getByLabel('새 태스크 담당').selectOption('colleague');const part=(await call('/sessions/'+sid)).parts.find(p=>p.number===partNumber);await page.getByLabel('관련 파트',{exact:true}).selectOption(part.id);await page.getByLabel('업무 설명',{exact:true}).fill(note);await openApp('태스크 만들기');const limit=Date.now()+460000;while(Date.now()<limit){const w=await call('/sessions/'+sid);const t=w.tasks.find(t=>t.title===title);if(t?.status==='done')return t;const failed=w.jobs.find(j=>j.agent_id==='colleague'&&j.status==='failed');if(failed)throw Error('AI failed '+failed.error);await sleep(1000)}throw Error('task timeout')}
try{
 const reg=await call('/auth/register','POST',{email:(compressed?'compressed-':'full120-')+Date.now()+'@example.com',password:'synthetic-'+crypto.randomUUID(),name:compressed?'개정 압축 검증 기업':'개정 합성 120분 검증 기업'},{});admin={Authorization:'Bearer '+reg.token};
 const inv=await call('/admin/invitations','POST',{alias,email:'synthetic@example.com'},admin);sid=inv.id;const token=new URL(inv.url).pathname.split('/').pop();auth={Authorization:'Bearer '+token};
 browser=await chromium.launch({headless:true});page=await(await browser.newContext()).newPage();await page.goto(inv.url);await page.getByRole('checkbox').check();await openApp('동의하고 팀 소개 보기');await openApp('준비됐어요. 시작하기');await page.getByRole('heading',{name:'# 팀 채널',exact:true}).waitFor();
 const initial=await call('/sessions/'+sid);start=wallStart=initial.started;evidence.session_id=sid;evidence.started_at=new Date(start*1000).toISOString();const pinned=(await call('/admin/sessions','GET',undefined,admin)).find(x=>x.id===sid);evidence.binding=pinned.binding;evidence.report_binding=pinned.report_binding;await checkpoint('started');
 await edit('문제 정의와 대상 사용자','첫 모임 참여자의 재참여율 22%를 배경으로 무료 다음 모임 알림을 작은 범위에서 실험합니다.');
 await at(120,'first-ai-chat');let reply=await answer(await send('chat','운영 정책 문서를 검색해 올해 유료 기능과 위치 정보 공개 원칙을 원문과 문서 이름으로 정리해 주세요.'));if(!reply.data.sources?.length)throw Error('missing live sources');
 await at(610,'division');await answer(await send('colleague','기본 분담안을 수락합니다. 1·2번은 제가, 3번은 공동, 4·5번은 동료 담당으로 확정해 주세요. 합의를 분업 도구로 기록해 주세요.'));
 if(!(await call('/sessions/'+sid)).division.agreed)throw Error('division not persisted');
 await assign('동료의 비교 기준 초안',4,'정책 문서를 확인해 무료 기능·동네 단위 공개를 기준으로 가상의 사례 비교 초안을 4번 파트에 저장해 주세요. complete_task로 실제 문서와 완료 상태를 함께 저장해 주세요.');
 await assign('동료의 지표와 출시 일정',5,'무료 알림의 실행 단계·담당·2주 실험 일정·재참여율 측정과 가정 확인 계획을 5번 파트에 작성해 주세요. 실제 자료와 가정을 구분하고 complete_task로 완료해 주세요.');
 await at(1810,'help-response');const beforeHelp=await call('/sessions/'+sid);await answer(await send('colleague','지금 문제 정의를 정리 중이라 10분 뒤 사례 비교를 함께 보겠습니다. 동료가 맡은 4번 파트의 비교 기준 보완을 동료 담당 태스크 하나와 10분 뒤 약속 시점으로 남겨 주세요. 그 전에는 변경을 보류하고, 약속 시점에 기존 본문을 유지하면서 무료 알림 비교 기준을 보완해 저장하고 완료를 알려 주세요.'));
 const promised=(await call('/sessions/'+sid)).tasks.find(t=>t.status==='waiting'&&!beforeHelp.tasks.some(p=>p.id===t.id));if(!promised||promised.due_elapsed<2300||promised.due_elapsed>2500)throw Error('promise not persisted');evidence.promise={task_id:promised.id,due_elapsed:promised.due_elapsed};
 await at(2420,'promise-followup');const promiseLimit=Date.now()+460000;let followup;while(Date.now()<promiseLimit){const w=await call('/sessions/'+sid);const trigger=w.records.find(r=>r.kind==='work_trigger'&&r.data.trigger?.startsWith('promise:'+promised.id+':'));if(trigger){followup=await answer(trigger.id);break}await sleep(1000)}if(!followup?.data.mutation_ids?.length)throw Error('promise followup missing');if((await call('/sessions/'+sid)).tasks.find(t=>t.id===promised.id).status!=='done')throw Error('promise task unfinished');

 await at(3010,'delay-response');await answer(await send('colleague','인터뷰 자료가 늦어져 2번은 01:05 공개 후 보완하겠습니다. 그동안 문제 정의와 무료 기능 범위를 먼저 진행합니다.'));
 await at(3910,'interview-released');if((await call('/sessions/'+sid+'/documents')).length!==5)throw Error('document release failed');
 await edit('사용자 인터뷰에서 찾은 근거','첫 참여자 8명 인터뷰의 “아는 사람이 없어 어색했다”, “다음 모임 일정을 놓쳤다”를 가설 근거로 사용합니다. 전체 사용자로 일반화하지 않으며 대상 집단의 적용 범위를 추가 확인합니다.');
 reply=await answer(await send('chat','공개된 인터뷰 요약과 운영 정책을 검색해 무료 알림 초안을 만들어 주세요. 첫 참여자 인터뷰의 적용 범위와 확인할 가정을 구분해 주세요.'));if(!reply.data.sources?.some(s=>s.id.includes(':interviews:')))throw Error('missing interview sources');
 await page.locator('article.message.assistant').last().getByRole('button',{name:'검토 후 팀에 공유'}).click();await answer(await send('colleague','AI 요약을 운영 정책 원문과 대조했고 무료 알림만 선택해 기획안에 반영하겠습니다.')); 
 await at(4210,'changed-target');await answer(await send('colleague','변경 대상은 3개월 넘게 모임에 나오지 않은 사용자로 이해했습니다. 문제 정의와 공동 기능을 이 범위로 수정하고 대상 집단의 수치 확인은 남겨 두겠습니다.'));
 await edit('문제 정의와 대상 사용자','대상은 3개월 넘게 모임에 안 나온 사용자입니다. 첫 참여자의 재참여율 22%는 배경 자료로만 사용하며 이 대상 집단의 수치는 추가 확인이 필요합니다.');
 await edit('핵심 기능 제안','무료 다음 모임 알림을 동네 단위에서 제공합니다. 유료 기능과 세부 위치 공개는 제외하고, 동의한 3개월 미참여 사용자의 재참여 의사를 작은 실험으로 확인합니다.');
 const beforeReview=(await call('/sessions/'+sid)).parts[2];await at(5410,'review-delivered');const review=(await call('/sessions/'+sid));if(review.parts[2].body!==beforeReview.body)throw Error('review overwrote candidate draft');if(!review.records.some(r=>r.kind==='review_draft'))throw Error('missing separate review');
 await answer(await send('colleague','운영 정책에 올해 유료 기능을 출시하지 않는다고 되어 있습니다. 별도 리뷰의 멤버십 할인은 정책과 충돌합니다. 무료 알림 대안으로 함께 바꾸면 어떨까요? 정책 원문을 확인해 주세요.'));
 await edit('핵심 기능 제안','별도 리뷰의 유료 멤버십 제안은 올해 유료 기능 없음 정책에 따라 제외했습니다. 3개월 미참여·알림 동의 사용자에게 무료 다음 모임 알림을 제공하고 동네 단위 정보만 공개합니다. 작은 실험에서 재참여 변화를 확인합니다.');
 await openApp('업무·제출');await openApp('공유 기획안');await page.screenshot({path:evidencePrefix+'-office.png',fullPage:true});
 await at(6610,'leader-report');const leaderMid=await send('leader','팀장님, 3개월 미참여 사용자를 위한 무료 다음 모임 알림을 제안합니다. 인터뷰는 가설 근거로만 사용했고 유료 기능과 세부 위치는 제외했습니다. 동료가 사례·지표·일정 파트를 작성했고 대상 집단의 수치 확인이 남았습니다.');
 const colleagueMid=await send('colleague','동료님, 맡아 주신 사례와 일정의 남은 확인 사항이 있으면 알려 주세요.','dm');
 const [leaderReply,colleagueReply]=await Promise.all([answer(leaderMid),answer(colleagueMid)]);if(leaderReply.actor!=='leader'||colleagueReply.actor!=='colleague')throw Error('independent sender routing failed');
 await at(7205,'automatic-deadline');await page.getByRole('heading',{name:'오늘의 업무를 마쳤습니다.'}).waitFor({timeout:15000});
 let report;const reportUntil=Date.now()+460000;while(Date.now()<reportUntil){report=await call('/admin/sessions/'+sid+'/report','GET',undefined,admin);if(report.state==='report_ready')break;if(report.state==='failed')throw Error('report generation failed');await sleep(1500)}if(report?.state!=='report_ready')throw Error('report timeout');
 const adminPage=await browser.newPage();await adminPage.goto('http://127.0.0.1:3000/admin');await adminPage.evaluate(t=>sessionStorage.setItem('admin',t),reg.token);await adminPage.reload();await adminPage.getByRole('row').filter({hasText:alias}).getByRole('button',{name:'확인',exact:true}).click();await adminPage.getByRole('heading',{name:'최종본 품질 대조'}).waitFor();await adminPage.screenshot({path:evidencePrefix+'-report.png',fullPage:true});
 if(report.report.items.filter(x=>x.axis==='협업 태도').some(x=>x.status==='관찰 기회 없음'))throw Error('event delivery not confirmed');
 if(!report.report.utilization_links?.some(x=>x.relation==='활용을 명시함'))throw Error('missing explicit AI utilization');
 if(report.report.quality_comparison.length!==3||report.report.quality_comparison.some(q=>!q.supporting.length&&!q.conflicting.length))throw Error('quality evidence missing');
 evidence.finished_at=new Date().toISOString();evidence.real_duration_seconds=Date.now()/1000-wallStart;evidence.full_120_minutes=!compressed&&evidence.real_duration_seconds>=7200;evidence.report_items=report.report.items.length;evidence.observed_items=report.report.items.filter(x=>x.observation).map(x=>x.item);evidence.quality_items=report.report.quality_comparison.length;evidence.completed=true;await fs.writeFile(evidencePrefix+'-report-content.json',JSON.stringify(report.report,null,2));await checkpoint('report-visible');
}catch(error){const raw=String(error?.message||'');const safe=/^(AI failed [a-z_]+|[a-z ]+)$/.test(raw)?raw:'verification phase failed: '+(error?.name||'Error');let detail=raw.replace(/\/invite\/[A-Za-z0-9_-]+/g,'/invite/[redacted]');for(const secret of [auth?.Authorization,admin?.Authorization])if(secret)detail=detail.split(secret).join('[redacted]').split(secret.replace('Bearer ','')).join('[redacted]');evidence.failure={phase,action,message:safe,detail:detail.slice(0,3000),at:new Date().toISOString()};if(page){try{const file=evidencePrefix+'-'+sid+'-failure.png';await page.screenshot({path:file,fullPage:true});evidence.failure.screenshot=file}catch{}}console.error(compressed?'compressed failed':'full120 failed',phase,action,safe);process.exitCode=1}
finally{await fs.writeFile(evidencePrefix+'-progress.json',JSON.stringify(evidence,null,2));if(browser)await browser.close();await api.dispose()}
