'use client';
import Link from 'next/link';
import {use,useEffect,useState} from 'react';
import {api,credential} from '@/lib/api';
export default function Debrief({params}:{params:Promise<{sessionId:string}>}){
 const {sessionId}=use(params);const [state,setState]=useState(''),[error,setError]=useState(''),[situations,setSituations]=useState<{title:string;text:string}[]>([]);
 useEffect(()=>{api('/sessions/'+sessionId+'/debrief',credential(sessionId)).then(w=>{setState(w.state);setSituations(w.situations)}).catch(e=>setError(String(e)))},[sessionId]);
 return <><header><Link href="/" className="brand">Day Zero<span> .</span></Link><small>세션 종료</small></header><main className="narrow">{error?<div role="alert" className="error">{error}</div>:!state?<p>제출 상태 확인 중…</p>:['active','invited'].includes(state)?<div className="card"><h1>아직 진행 중인 세션입니다.</h1><Link href={'/office/'+sessionId}>업무 공간으로 돌아가기</Link></div>:<><p className="eyebrow">Thank you for working together</p><h1>오늘의 업무를 마쳤습니다.</h1><p className="muted">결과물과 업무 기록이 제출되었습니다. 함께한 과정에 관해 안내드립니다.</p><section className="card"><h2>세션에 포함된 상황</h2><ol>{situations.map(item=><li key={item.title}><strong>{item.title}</strong><p>{item.text}</p></li>)}</ol><p className="muted">제출 시점에 따라 모든 상황을 경험하지 않았을 수 있습니다. 전달되지 않은 상황은 관찰 기회 없음으로 구분합니다.</p></section><section className="notice"><h2>기록은 어떻게 사용되나요?</h2><p>업무 수행 역량·AI 활용 역량·협업 태도의 근거를 정리해 기업에 전달합니다. AI 채팅을 쓰지 않았다는 이유로 부정적으로 판단하지 않습니다.</p><p>점수·순위·합격 판정을 제공하지 않습니다. 실제 기록을 확인하고 최종 판단하는 사람은 채용 담당자입니다.</p></section></>}</main></>
}
