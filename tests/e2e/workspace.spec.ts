import {test,expect} from '../../apps/web/node_modules/@playwright/test';
import {execFileSync} from 'node:child_process';
import path from 'node:path';
const root=path.resolve(__dirname,'../..');
async function enter(page:any,request:any,alias='개정 브라우저 합성 검증'){
 const reg=await request.post('http://127.0.0.1:8000/auth/register',{data:{email:'e2e-'+crypto.randomUUID()+'@example.com',password:'e2e-password-123',name:'E2E 합성 기업'}});expect(reg.ok()).toBeTruthy();const admin=(await reg.json()).token;
 const inv=await(await request.post('http://127.0.0.1:8000/admin/invitations',{headers:{Authorization:'Bearer '+admin},data:{alias,email:'synthetic@example.com'}})).json();
 const token=new URL(inv.url).pathname.split('/').pop(),auth={Authorization:'Bearer '+token};
 await page.goto(inv.url);await expect(page.getByRole('button',{name:'동의하고 팀 소개 보기'})).toBeDisabled();await page.getByRole('checkbox').check();await page.getByRole('button',{name:'동의하고 팀 소개 보기'}).click();await page.getByRole('button',{name:'준비됐어요. 시작하기'}).click();await expect(page.getByRole('heading',{name:'# 팀 채널',exact:true})).toBeVisible();
 return {sid:inv.id,auth,admin};
}
function advance(sid:string,seconds:number){execFileSync(path.join(root,'services/dayzero/.venv/bin/python'),['-c',`import time; from dayzero.backend.db import DB, Session; from dayzero.backend.domain import tick_session\nwith DB.begin() as db:\n s=db.get(Session,'${sid}'); s.started=time.time()-${seconds}; tick_session(db,s)`],{cwd:root})}

test('세 앱·채널 초안, 문서·태스크, 비활성 메신저 알림과 읽음, 제출',async({page,request})=>{
 const {sid,auth}=await enter(page,request);
 await page.getByLabel('팀 메시지',{exact:true}).fill('팀 채널 초안 보존');
 await page.getByRole('button',{name:'이도현 1:1',exact:true}).click();await page.getByLabel('동료 메시지',{exact:true}).fill('개인 동료 DM 초안');
 await page.getByRole('button',{name:'AI 채팅',exact:true}).click();await page.getByLabel('AI 메시지',{exact:true}).fill('개인 AI 초안 보존');
 await page.getByRole('button',{name:'업무·제출',exact:true}).click();await page.getByRole('button',{name:'공유 기획안',exact:true}).click();
 const part=page.getByRole('textbox',{name:'문제 정의와 대상 사용자',exact:true});await part.fill('재참여율 22%를 배경으로 무료 알림을 실험합니다.');
 await page.getByRole('button',{name:'메신저',exact:true}).click();await expect(page.getByLabel('동료 메시지',{exact:true})).toHaveValue('개인 동료 DM 초안');await page.getByRole('button',{name:'# 팀 채널',exact:true}).click();await expect(page.getByLabel('팀 메시지',{exact:true})).toHaveValue('팀 채널 초안 보존');
 await page.getByRole('button',{name:'AI 채팅',exact:true}).click();await expect(page.getByLabel('AI 메시지',{exact:true})).toHaveValue('개인 AI 초안 보존');
 advance(sid,1802);await expect(page.getByText(/메신저 새 메시지 \d+개/)).toBeVisible({timeout:10000});
 let w=await(await request.get('http://127.0.0.1:8000/sessions/'+sid,{headers:auth})).json();const help=w.records.find((r:any)=>r.data.event_key==='help');expect(help).toBeTruthy();expect(w.records.some((r:any)=>r.kind==='read'&&r.data.message_id===help.id)).toBeFalsy();
 await page.getByRole('button',{name:'메신저 열기'}).click();await expect(page.getByText('비슷한 서비스 사례를 찾는데 기준이 헷갈려요.',{exact:false})).toBeVisible();
 await expect.poll(async()=>{w=await(await request.get('http://127.0.0.1:8000/sessions/'+sid,{headers:auth})).json();return w.records.some((r:any)=>r.kind==='read'&&r.data.message_id===help.id)}).toBeTruthy();
 await page.getByLabel('팀 메시지',{exact:true}).fill('비교 기준을 먼저 확인하겠습니다.');await page.getByRole('button',{name:'보내기',exact:true}).click();
 const message=page.locator('article.message.candidate').filter({hasText:'비교 기준을 먼저 확인하겠습니다.'});await expect(message).toBeVisible();await message.getByRole('button',{name:'태스크로 등록'}).click();
 await page.getByLabel('태스크 제목',{exact:true}).fill('비교 기준 확인');await page.getByLabel('관련 파트',{exact:true}).selectOption({label:'4. 비슷한 서비스 사례'});await page.getByRole('button',{name:'태스크 만들기',exact:true}).click();await expect(page.getByRole('heading',{name:/비교 기준 확인/})).toBeVisible();await page.getByLabel('비교 기준 확인 상태').selectOption('doing');await page.getByRole('button',{name:'태스크 저장'}).click();
 await page.getByRole('button',{name:'공유 기획안',exact:true}).click();await expect(part).toHaveValue('재참여율 22%를 배경으로 무료 알림을 실험합니다.');await expect(page.getByText('저장됨 · v2',{exact:true})).toBeVisible();
 await page.getByRole('button',{name:'회사 문서',exact:true}).click();await expect(page.getByText('사용자 인터뷰 요약 · 공개 대기')).toBeVisible();await page.getByText('운영 정책',{exact:false}).first().click();await page.getByRole('button',{name:'AI 채팅',exact:true}).click();await page.getByRole('button',{name:'업무·제출',exact:true}).click();await expect(page.getByText('올해는 유료 기능을 출시하지 않는다',{exact:false})).toBeVisible();
 await page.getByRole('button',{name:'최종 제출',exact:true}).click();await page.screenshot({path:path.join(root,'docs/evidence/revision-mini-pc.png'),fullPage:true});await page.getByRole('button',{name:'기획안 제출',exact:true}).click();await page.getByRole('button',{name:'제출 확정',exact:true}).click();await expect(page.getByRole('heading',{name:'오늘의 업무를 마쳤습니다.'})).toBeVisible();
 w=await(await request.get('http://127.0.0.1:8000/sessions/'+sid,{headers:auth})).json();expect(w.tasks).toHaveLength(1);expect(w.tasks[0].source_id).toBeTruthy();expect(w.tasks[0].status).toBe('doing');
});

test('편집·앱 전환 직후 제출하면 마지막 저장을 확정한다',async({page,request})=>{
 const {sid,auth}=await enter(page,request,'마지막 저장 합성 검증');await page.getByRole('button',{name:'업무·제출',exact:true}).click();await page.getByRole('button',{name:'공유 기획안',exact:true}).click();
 const part=page.getByRole('textbox',{name:'문제 정의와 대상 사용자',exact:true});await part.fill('앱 이동 직전 입력');await page.getByRole('button',{name:'AI 채팅',exact:true}).click();await page.getByRole('button',{name:'업무·제출',exact:true}).click();await expect(part).toHaveValue('앱 이동 직전 입력');await part.fill('제출 직전 마지막 입력');await page.getByRole('button',{name:'최종 제출',exact:true}).click();await page.getByRole('button',{name:'기획안 제출',exact:true}).click();await page.getByRole('button',{name:'제출 확정',exact:true}).click();await expect(page.getByRole('heading',{name:'오늘의 업무를 마쳤습니다.'})).toBeVisible();
 const w=await(await request.get('http://127.0.0.1:8000/sessions/'+sid,{headers:auth})).json();expect(w.parts[0].body).toBe('제출 직전 마지막 입력');
});

test('동시 편집 충돌에서 입력을 보존하고 명시적으로 다시 저장한다',async({page,request})=>{
 const {sid,auth}=await enter(page,request,'동시 편집 합성 검증');
 await page.getByRole('button',{name:'업무·제출',exact:true}).click();await page.getByRole('button',{name:'공유 기획안',exact:true}).click();
 const w=await(await request.get('http://127.0.0.1:8000/sessions/'+sid,{headers:auth})).json(),p=w.parts[0];
 let release!:()=>void,intercepted!:()=>void;const hold=new Promise<void>(resolve=>release=resolve),arrived=new Promise<void>(resolve=>intercepted=resolve);
 await page.route('**/api/sessions/'+sid+'/parts/'+p.id,async route=>{intercepted();await hold;await route.continue()},{times:1});
 const editor=page.getByRole('textbox',{name:p.title,exact:true});await editor.fill('내가 입력 중인 초안');await arrived;
 const saved=await request.patch('http://127.0.0.1:8000/sessions/'+sid+'/parts/'+p.id,{headers:auth,data:{body:'다른 창에서 저장한 최신본',version:p.version,operation_id:crypto.randomUUID()}});expect(saved.ok()).toBeTruthy();
 release();await expect(page.getByText('다른 작성자가 문서를 변경했습니다. 입력은 보존되어 있습니다.')).toBeVisible();await expect(editor).toHaveValue('내가 입력 중인 초안');
 await expect.poll(async()=>page.locator('section.part').filter({has:editor}).getByRole('status').innerText()).toContain('v2');
 await page.getByRole('button',{name:'내 입력으로 다시 저장',exact:true}).click();await expect(page.getByText('저장됨 · v3',{exact:true})).toBeVisible();
 const final=await(await request.get('http://127.0.0.1:8000/sessions/'+sid,{headers:auth})).json();expect(final.parts[0].body).toBe('내가 입력 중인 초안');expect(final.records.filter((r:any)=>r.kind==='part')).toHaveLength(2);
});

test('미확인 메시지 수가 늘어도 앱·채널 버튼 이름으로 복귀한다',async({page,request})=>{
 const {sid}=await enter(page,request,'미확인 알림 탐색 합성 검증');
 await page.getByRole('button',{name:'AI 채팅',exact:true}).click();advance(sid,610);
 await expect(page.getByText(/메신저 새 메시지 \d+개/)).toBeVisible({timeout:10000});
 await expect(page.getByRole('button',{name:'메신저',exact:true})).toBeVisible();
 await page.getByRole('button',{name:'메신저',exact:true}).click();
 await page.getByRole('button',{name:'# 팀 채널',exact:true}).click();
 await expect(page.getByText('기본 분담안을 제안해요.',{exact:false})).toBeVisible();
});
