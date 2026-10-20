'use client';
import Link from 'next/link';
import {useRouter} from 'next/navigation';
import {useEffect,useState,FormEvent} from 'react';
import {api,statusLabel} from '@/lib/api';
import SessionReport from '@/components/session-report';
import type {WorkScenario} from '@/lib/scenario';

export default function CompanyPortal({reportsOnly=false,sessionId}:{reportsOnly?:boolean;sessionId?:string}){
 const router=useRouter();
 const [token,setToken]=useState(''),[mode,setMode]=useState('login'),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 const [rows,setRows]=useState<any[]>([]),[url,setUrl]=useState(''),[report,setReport]=useState<any>(null);
 const [scenarios,setScenarios]=useState<WorkScenario[]>([]),[scenarioId,setScenarioId]=useState('looplab-v2');
 const [reportRevision,setReportRevision]=useState(0);
 useEffect(()=>setToken(sessionStorage.getItem('admin')||''),[]);
 useEffect(()=>{if(!token)return;let live=true;api<WorkScenario[]>('/admin/scenarios',token).then(items=>{if(live){setScenarios(items);setScenarioId(current=>items.some(s=>s.id===current)?current:items[0]?.id||'')}}).catch(e=>{if(live)setError(String(e))});return()=>{live=false}},[token]);
 useEffect(()=>{
  if(!token)return;let live=true;
  async function load(){try{const r=await api<any[]>('/admin/sessions',token);if(live)setRows(r)}catch(e){if(live)setError(String(e))}}
  load();const id=setInterval(load,3000);return()=>{live=false;clearInterval(id)};
 },[token]);
 useEffect(()=>{
  if(!token||!sessionId)return;let live=true,timer:ReturnType<typeof setTimeout>;
  setError('');
  async function load(){try{const r=await api('/admin/sessions/'+sessionId+'/report',token);if(live){setReport(r);if(r.state==='completed')timer=setTimeout(load,3000)}}catch(e){if(live)setError(String(e))}}
  load();return()=>{live=false;clearTimeout(timer)};
 },[token,sessionId,reportRevision]);
 async function auth(e:FormEvent<HTMLFormElement>){
  e.preventDefault();setBusy(true);setError('');const f=new FormData(e.currentTarget);
  try{const r=await api('/auth/'+mode,undefined,'POST',{email:f.get('email'),password:f.get('password'),name:f.get('name')||'우리 회사'});sessionStorage.setItem('admin',r.token);setToken(r.token)}catch(e){setError(String(e))}finally{setBusy(false)}
 }
 async function invite(e:FormEvent<HTMLFormElement>){
  e.preventDefault();setBusy(true);setError('');const f=new FormData(e.currentTarget);
  try{const r=await api('/admin/invitations',token,'POST',{alias:f.get('alias'),email:f.get('email'),scenario_id:scenarioId});setUrl(r.url);setRows(await api('/admin/sessions',token))}catch(e){setError(String(e))}finally{setBusy(false)}
 }
 function refresh(){setReportRevision(n=>n+1)}
 async function retry(){setBusy(true);try{await api('/admin/sessions/'+sessionId+'/report/retry',token,'POST');await refresh()}catch(e){setError(String(e))}finally{setBusy(false)}}
 const completed=rows.filter(r=>['completed','report_ready','failed'].includes(r.state)),visibleRows=reportsOnly?completed:rows;
 const person=rows.find(r=>r.id===sessionId);
 return <>
  <header><Link className="brand" href="/">Day Zero<span> .</span></Link><small>기업 관계자</small>{token&&<button className="secondary" onClick={()=>{sessionStorage.removeItem('admin');setToken('');setRows([]);setReport(null)}}>로그아웃</button>}</header>
  <main>{error&&<div role="alert" className="error">{error}</div>}
  {!token?<div className="narrow card"><p className="eyebrow">기업 관계자</p><h1>{mode==='login'?'기업 계정으로 로그인':'팀의 첫 하루를 준비하세요.'}</h1><p className="muted">지원자를 초대하고, 완료자별 업무 기록과 보고서를 확인합니다.</p><div className="tabs"><button className={mode==='login'?'':'secondary'} onClick={()=>setMode('login')}>로그인</button><button className={mode==='register'?'':'secondary'} onClick={()=>setMode('register')}>가입</button></div><form onSubmit={auth}>{mode==='register'&&<><label htmlFor="name">회사 이름</label><input id="name" name="name" required/></>}<label htmlFor="email">이메일</label><input id="email" name="email" type="email" autoComplete="email" required/><label htmlFor="password">비밀번호</label><input id="password" name="password" type="password" minLength={10} autoComplete={mode==='login'?'current-password':'new-password'} required/><p className="muted">비밀번호는 10자 이상입니다.</p><button disabled={busy}>{busy?'처리 중…':mode==='login'?'로그인':'가입하기'}</button></form></div>:<>
   <nav className="company-nav" aria-label="기업 메뉴"><Link href="/admin" aria-current={!reportsOnly&&!sessionId?'page':undefined}>지원자 관리 <span className="badge">{rows.length}</span></Link><Link href="/admin/reports" aria-current={reportsOnly||sessionId?'page':undefined}>완료자 보고서 <span className="badge">{completed.length}</span></Link></nav>
   {sessionId?<>
    <Link href="/admin/reports">← 완료자 보고서 목록</Link><div className="hero"><p className="eyebrow">개별 업무 보고서</p><h1>{person?person.alias+'님의 보고서':'지원자 보고서'}</h1>{person&&<p className="muted">{person.email} · {statusLabel[person.state]}</p>}</div>
    {report?<SessionReport data={report} onRefresh={refresh} onRetry={retry} busy={busy}/>:!error&&<p>보고서를 불러오고 있습니다…</p>}
   </>:<>
    <div className="hero"><p className="eyebrow">기업 관계자</p><h1>{reportsOnly?'완료자별 보고서':'지원자와 함께할 첫 하루'}</h1><p className="muted">{reportsOnly?'제출을 마친 지원자의 최종본과 업무 수행·AI 활용·협업 근거를 각각 확인하세요.':'지원자를 초대하고 진행 상태를 확인하세요. 제출된 결과는 완료자 보고서에 모입니다.'}</p></div>
    {!reportsOnly&&<div className="grid"><section className="card"><h2>지원자 초대</h2><form onSubmit={invite}><label htmlFor="scenario">업무 시나리오</label><select id="scenario" value={scenarioId} onChange={e=>setScenarioId(e.target.value)} required disabled={!scenarios.length}>{!scenarios.length&&<option value="">시나리오를 불러오는 중…</option>}{scenarios.map(s=><option key={s.id} value={s.id}>{s.company} · {s.title}</option>)}</select><p className="muted">{scenarios.find(s=>s.id===scenarioId)?.deliverable_description}</p><label htmlFor="alias">지원자 별칭</label><input id="alias" name="alias" required/><label htmlFor="invite-email">지원자 이메일</label><input id="invite-email" name="email" type="email" required/><p className="muted">72시간 동안 유효한 링크를 만들어 직접 전달합니다.</p><button disabled={busy||!scenarios.length}>초대 링크 만들기</button></form>{url&&<div className="notice" style={{marginTop:16}}><label htmlFor="invite-link">생성된 초대 링크</label><input id="invite-link" readOnly value={url} className="linkbox"/><button className="secondary" onClick={()=>navigator.clipboard.writeText(url)}>링크 복사</button><a style={{marginLeft:12}} href={url} target="_blank" rel="noreferrer">입장 화면 열기 ↗</a></div>}</section><section className="card"><h2>보고서가 담는 것</h2><p>최종본 품질 · 업무 수행 · AI 활용 · 협업 과정</p><p className="muted">메시지 원문과 문서 버전, 작성 주체를 함께 확인합니다. 점수·순위·합격 여부는 제공하지 않습니다.</p><Link href="/admin/reports">완료자 보고서 확인 →</Link></section></div>}
    <section className="card"><div className="row spread"><h2>{reportsOnly?'제출을 마친 지원자':'지원자 목록'}</h2><span className="badge">{visibleRows.length}명</span></div>{!visibleRows.length?<p className="empty">{reportsOnly?'아직 제출을 마친 지원자가 없습니다.':'첫 지원자를 초대해 보세요.'}</p>:<div className="table-scroll"><table><thead><tr><th>지원자</th><th>이메일</th><th>상태</th><th>보고서</th></tr></thead><tbody>{visibleRows.map(r=><tr key={r.id}><td>{r.alias}{r.scenario&&<small className="muted" style={{display:'block'}}>{r.scenario.title}</small>}</td><td>{r.email}</td><td><span className="badge">{r.state==='completed'?'보고서 생성 중':statusLabel[r.state]}</span></td><td><button className="secondary" onClick={()=>router.push('/admin/reports/'+r.id)}>{reportsOnly?(r.state==='report_ready'?'보고서 보기':'생성 상태 확인'):'확인'}</button></td></tr>)}</tbody></table></div>}</section>
   </>}
  </>}
  </main>
 </>;
}
