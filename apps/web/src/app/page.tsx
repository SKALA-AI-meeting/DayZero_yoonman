import Link from 'next/link';
export default function Home(){return <>
 <header><Link className="brand" href="/">Day Zero<span> .</span></Link><small>함께 일하며 발견하는 가능성</small></header>
 <main><div className="hero"><p className="eyebrow">A day of working together</p><h1>이력서 다음의 이야기,<br/>함께 일하는 하루.</h1><p className="muted">개인은 업무를 수행하고, 기업은 실제 과정과 결과의 근거를 확인합니다.</p></div>
 <div className="grid role-entry"><section className="card"><span className="badge">01 · 개인</span><h2>나의 업무 공간</h2><p>초대 링크로 입장해 AI 팀원과 업무를 수행하세요.</p><p className="muted">메신저 · AI 채팅 · 업무·제출</p><Link className="entry-link" href="/personal">개인으로 입장 →</Link></section><section className="card"><span className="badge">02 · 기업 관계자</span><h2>우리 기업의 지원자와 보고서</h2><p>지원자를 초대하고, 완료한 사람별 보고서를 확인하세요.</p><p className="muted">지원자 관리 · 완료자 목록 · 개별 보고서</p><Link className="entry-link" href="/admin">기업 관계자로 입장 →</Link></section></div>
 <footer className="page-footer">점수와 순위 없이, 실제 업무의 기록을 바탕으로. 최종 판단은 사람이 합니다.</footer></main>
 </>}
