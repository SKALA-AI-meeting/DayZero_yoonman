'use client';
import Link from 'next/link';
import {FormEvent,useState} from 'react';
import {useRouter} from 'next/navigation';
export default function Personal(){
 const router=useRouter(),[link,setLink]=useState(''),[error,setError]=useState('');
 function enter(e:FormEvent){e.preventDefault();try{const target=new URL(link.trim(),location.origin);if(target.origin!==location.origin||!/^\/invite\/[A-Za-z0-9_-]+$/.test(target.pathname)||target.search||target.hash)throw Error();router.push(target.pathname)}catch{setError('이 서비스에서 받은 초대 링크를 입력해 주세요.')}}
 return <><header><Link className="brand" href="/">Day Zero<span> .</span></Link><small>개인</small></header><main className="narrow"><p className="eyebrow">나의 업무 공간</p><h1>초대받은 하루를 시작하세요.</h1><p className="muted">기업 담당자가 전달한 초대 링크로 입장합니다. 안내와 동의를 마친 뒤 준비되면 업무를 시작할 수 있습니다.</p><section className="card"><h2>초대 링크로 입장</h2>{error&&<div className="error" role="alert">{error}</div>}<form onSubmit={enter}><label htmlFor="personal-invite">초대 링크</label><input id="personal-invite" value={link} onChange={e=>{setLink(e.target.value);setError('')}} placeholder="전달받은 초대 링크를 붙여 넣으세요" autoComplete="off" required/><p className="muted">링크가 없거나 만료됐다면 기업 담당자에게 문의해 주세요.</p><button>초대 확인하기</button></form></section><Link href="/">← 개인·기업 선택으로</Link></main></>;
}
