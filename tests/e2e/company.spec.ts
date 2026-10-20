import {test,expect} from '../../apps/web/node_modules/@playwright/test';
import {execFileSync} from 'node:child_process';
import path from 'node:path';
const root=path.resolve(__dirname,'../..');
// Match the canonical origin used by the backend's invitation URLs.
test.use({baseURL:'http://localhost:3000'});

test('개인·기업 진입, 완료자별 독립 보고서, 상태 구분과 기업 격리',async({page,request})=>{
 const email='ui-company-'+crypto.randomUUID()+'@example.com',password='synthetic-password-'+crypto.randomUUID();
 const reg=await request.post('http://127.0.0.1:8000/auth/register',{data:{email,password,name:'기업 화면 합성 검증'}});expect(reg.ok()).toBeTruthy();const auth={Authorization:'Bearer '+(await reg.json()).token};
 async function invite(alias:string){const r=await request.post('http://127.0.0.1:8000/admin/invitations',{headers:auth,data:{alias,email:'synthetic@example.com'}});expect(r.ok()).toBeTruthy();return r.json()}
 const a=await invite('합성 완료자 A'),b=await invite('합성 완료자 B'),pending=await invite('합성 초대 대기'),generating=await invite('합성 생성 중'),failed=await invite('합성 생성 실패');
 execFileSync(path.join(root,'services/dayzero/.venv/bin/python'),['scripts/seed_company_ui_reports.py',JSON.stringify([
  {id:a.id,body:'합성 A의 무료 모임 알림 기획안'}, {id:b.id,body:'합성 B의 동네 단위 공개 기획안'},
  {id:generating.id,body:'합성 생성 대기 기획안',state:'completed'}, {id:failed.id,body:'합성 실패 상태 기획안',state:'failed'}
 ])],{cwd:root});
 await page.goto('/');await expect(page.getByRole('link',{name:'개인으로 입장 →',exact:true})).toBeVisible();await expect(page.getByRole('link',{name:'기업 관계자로 입장 →',exact:true})).toBeVisible();
 await page.getByRole('link',{name:'개인으로 입장 →',exact:true}).click();await page.getByLabel('초대 링크',{exact:true}).fill('https://example.com/invite/foreign');await page.getByRole('button',{name:'초대 확인하기'}).click();await expect(page.getByRole('main').getByRole('alert')).toHaveText('이 서비스에서 받은 초대 링크를 입력해 주세요.');
 await page.getByLabel('초대 링크',{exact:true}).fill(pending.url);await page.getByRole('button',{name:'초대 확인하기'}).click();await expect(page.getByRole('heading',{name:/합성 초대 대기님/})).toBeVisible();
 await page.getByRole('link',{name:'Day Zero .'}).click();await page.getByRole('link',{name:'기업 관계자로 입장 →',exact:true}).click();await page.getByLabel('이메일',{exact:true}).fill(email);await page.getByLabel('비밀번호',{exact:true}).fill(password);await page.locator('form').getByRole('button',{name:'로그인',exact:true}).click();await expect(page.getByRole('heading',{name:'지원자와 함께할 첫 하루',exact:true})).toBeVisible();
 await page.getByRole('navigation',{name:'기업 메뉴'}).getByRole('link',{name:/완료자 보고서/}).click();await expect(page.getByRole('heading',{name:'완료자별 보고서',exact:true})).toBeVisible();await expect(page.getByRole('row').filter({hasText:'합성 초대 대기'})).toHaveCount(0);await expect(page.getByRole('row').filter({hasText:'합성 생성 중'})).toContainText('보고서 생성 중');await expect(page.getByRole('row').filter({hasText:'합성 생성 실패'})).toContainText('실패');
 await page.screenshot({path:path.join(root,'docs/evidence/revision-company-reports.png'),fullPage:true});
 for(const [person,body] of [[a,'합성 A의 무료 모임 알림 기획안'],[b,'합성 B의 동네 단위 공개 기획안']] as const){
  await page.getByRole('row').filter({hasText:person===a?'합성 완료자 A':'합성 완료자 B'}).getByRole('button',{name:'보고서 보기',exact:true}).click();await expect(page).toHaveURL(new RegExp('/admin/reports/'+person.id+'$'));await expect(page.getByRole('heading',{name:person===a?'합성 완료자 A님의 보고서':'합성 완료자 B님의 보고서',exact:true})).toBeVisible();await expect(page.getByRole('heading',{name:'최종본 품질 대조',exact:true})).toBeVisible();await page.getByText('1. 문제 정의와 대상 사용자 · v2',{exact:true}).click();await expect(page.getByText(body,{exact:true}).first()).toBeVisible();await page.reload();await expect(page.getByRole('heading',{name:person===a?'합성 완료자 A님의 보고서':'합성 완료자 B님의 보고서',exact:true})).toBeVisible();await page.getByRole('link',{name:'← 완료자 보고서 목록',exact:true}).click();
 }
 const other=await request.post('http://127.0.0.1:8000/auth/register',{data:{email:'ui-company-'+crypto.randomUUID()+'@example.com',password,name:'기업 화면 합성 검증'}});
 const foreign=await(await request.post('http://127.0.0.1:8000/admin/invitations',{headers:{Authorization:'Bearer '+(await other.json()).token},data:{alias:'다른 기업 전용 이름',email:'synthetic@example.com'}})).json();
 await page.goto('/admin/reports/'+foreign.id);await expect(page.getByRole('main').getByRole('alert')).toBeVisible();await expect(page.getByText('다른 기업 전용 이름',{exact:false})).toHaveCount(0);
 await page.getByRole('button',{name:'로그아웃',exact:true}).click();await expect(page.getByRole('heading',{name:'기업 계정으로 로그인',exact:true})).toBeVisible();
});
