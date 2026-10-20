import Link from 'next/link';
import OfficeIcon from '@/components/office-icon';
import './home.css';

export default function Home(){return <div className="home-page">
 <a className="home-skip" href="#home-main">본문으로 이동</a>
 <header className="home-header">
  <div className="home-header-inner"><Link className="brand" href="/" aria-label="Day Zero 홈">Day Zero<span> .</span></Link><nav aria-label="홈 메뉴"><a href="#how-it-works">이용 흐름</a><Link className="home-header-entry" href="/admin">기업 관계자 입장<OfficeIcon name="arrow"/></Link></nav></div>
 </header>
 <main id="home-main" className="home-main">
  <section className="home-hero home-container" aria-labelledby="home-title">
   <div className="home-hero-copy">
    <p className="home-eyebrow">A day of working together</p>
    <h1 id="home-title">함께 일하는 하루가,<br/>나를 보여주는<br/><span>경험이 되도록.</span></h1>
    <p className="home-intro">이력서 다음의 이야기는 일하는 과정에 있습니다.<br className="home-desktop-break"/> AI 팀원과 문제를 풀고, 함께 만든 결과로 나의 가능성을 보여주세요.</p>
    <div className="home-actions"><Link className="home-button home-button-primary" href="/personal">초대 링크로 입장<OfficeIcon name="arrow"/></Link><Link className="home-button home-button-secondary" href="/admin">기업 관계자로 시작</Link></div>
    <p className="home-entry-note">개인 참여는 기업 담당자가 전달한 초대 링크가 필요해요.</p>
   </div>
   <aside className="home-day-brief" aria-labelledby="home-brief-title">
    <div className="home-brief-top"><span>오늘의 업무 경험</span><span className="home-duration"><OfficeIcon name="clock"/>120분</span></div>
    <h2 id="home-brief-title">하나의 과제,<br/>함께 만드는 결과물.</h2>
    <p>가상의 회사에 합류해 업무를 나누고,<br className="home-desktop-break"/> 자료를 살펴보며 하나의 프로젝트를 완성합니다.</p>
    <dl className="home-brief-details">
     <div><dt>함께하는 팀</dt><dd>업무를 안내하는 AI 팀장<br/>함께 의논하고 작성하는 AI 동료</dd></div>
     <div><dt>나만의 도구</dt><dd>생각을 정리하고 초안을 돕는 개인 AI</dd></div>
     <div><dt>남기는 결과</dt><dd>최종 결과물과 그 과정의 업무 기록</dd></div>
    </dl>
    <p className="home-brief-note"><OfficeIcon name="check"/>안내를 확인하고, 준비되면 시작하세요.</p>
   </aside>
  </section>

  <section id="how-it-works" className="home-flow home-container" aria-labelledby="home-flow-title">
   <div className="home-section-heading"><div><p className="home-eyebrow">How it works</p><h2 id="home-flow-title">초대에서 시작해,<br/>나의 업무 기록으로 남는 하루.</h2></div><p>처음 만나는 팀, 함께 풀어가는 과제.<br/>세 단계로 Day Zero를 경험합니다.</p></div>
   <ol className="home-steps">
    <li><span className="home-step-number" aria-hidden="true">01</span><h3>초대받은 공간에 입장</h3><p>기업에서 받은 링크로 들어와 업무 안내와 팀 소개를 확인합니다.</p></li>
    <li><span className="home-step-number" aria-hidden="true">02</span><h3>AI 팀원과 함께 업무</h3><p>동료와 역할을 나누고, 자료와 개인 AI를 활용해 공유 문서를 작성합니다.</p></li>
    <li><span className="home-step-number" aria-hidden="true">03</span><h3>결과물을 제출하고 마무리</h3><p>최종 문서를 제출하면 기업은 결과물과 업무 과정이 담긴 보고서를 확인합니다.</p></li>
   </ol>
  </section>

  <section className="home-spaces" aria-labelledby="home-spaces-title">
   <div className="home-container home-spaces-inner">
    <div className="home-spaces-intro"><p className="home-eyebrow">Your workspace</p><h2 id="home-spaces-title">대화에서 생각으로,<br/>생각에서 결과물로.</h2><p>세 개의 공간을 오가며 일합니다.<br/>좋은 아이디어를 발견했다면,<br/>검토하고 공유 문서에 담아보세요.</p><a className="home-text-link" href="#get-started">나에게 맞는 입장 선택<OfficeIcon name="arrow"/></a></div>
    <dl className="home-space-list">
     <div><dt><span className="home-space-icon"><OfficeIcon name="messenger"/></span><span>메신저<small>팀과 함께 의논하는 공간</small></span></dt><dd>팀장에게 질문하고 동료와 업무를 나눕니다. 동료가 맡은 내용을 공유 문서에 작성하고, 함께 검토합니다.</dd></div>
     <div><dt><span className="home-space-icon"><OfficeIcon name="chat"/></span><span>개인 AI<small>생각과 초안을 정리하는 도구</small></span></dt><dd>요약, 아이디어, 초안이 필요할 때 도움을 요청합니다. 답변을 검토한 뒤 직접 팀에 공유하거나 문서에 반영합니다.</dd></div>
     <div><dt><span className="home-space-icon"><OfficeIcon name="file"/></span><span>업무·제출<small>작성하고 확인하고 제출하는 곳</small></span></dt><dd>업무 현황과 회사 자료를 확인하고 공유 문서를 완성합니다. 이곳에 저장한 문서가 최종 제출물이 됩니다.</dd></div>
    </dl>
   </div>
  </section>

  <section className="home-principle home-container" aria-labelledby="home-principle-title">
   <p className="home-eyebrow">Our approach</p><h2 id="home-principle-title">결과만큼, 그 과정도 보이도록.</h2><p>어떤 질문을 했는지, AI의 제안을 어떻게 활용했는지, 동료와 무엇을 조율했는지.<br className="home-desktop-break"/> Day Zero는 최종 결과물과 함께 실제 업무의 기록을 제공합니다.</p><div className="home-principle-note"><OfficeIcon name="check"/><span>점수와 순위 없이, 기록과 근거를 바탕으로. 최종 판단은 사람이 합니다.</span></div>
  </section>

  <section id="get-started" className="home-start home-container" aria-labelledby="home-start-title">
   <div className="home-start-heading"><p className="home-eyebrow">Start your day</p><h2 id="home-start-title">오늘의 가능성을<br/>발견할 준비가 됐나요?</h2></div>
   <div className="home-role-list">
    <section className="home-role"><div><span className="home-role-label">개인</span><h3>초대받은 하루를 시작하세요.</h3><p>전달받은 초대 링크를 확인하고 나의 업무 공간으로 입장합니다.</p></div><Link className="home-text-link" href="/personal">개인으로 입장 →</Link></section>
    <section className="home-role"><div><span className="home-role-label">기업 관계자</span><h3>함께 일할 가능성을 발견하세요.</h3><p>지원자를 초대하고, 완료된 결과물과 사람별 업무 기록을 살펴봅니다.</p></div><Link className="home-text-link" href="/admin">기업 관계자로 입장 →</Link></section>
   </div>
  </section>
 </main>
 <footer className="home-footer"><div className="home-container"><Link className="brand" href="/">Day Zero<span> .</span></Link><p>함께 일하며 발견하는 가능성</p><a href="#home-main">처음으로 ↑</a></div></footer>
</div>}
