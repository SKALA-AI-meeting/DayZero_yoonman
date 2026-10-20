export async function api<T=any>(path: string, token?: string, method='GET', body?: unknown): Promise<T> {
  const response = await fetch('/api'+path, {method, headers: {'Content-Type':'application/json', ...(token ? {Authorization:'Bearer '+token}: {})}, body: body===undefined ? undefined : JSON.stringify(body)});
  const value = await response.json();
  if (!response.ok) throw new Error(value.detail || '요청을 처리하지 못했습니다.');
  return value;
}
export const statusLabel: Record<string,string> = {invited:'초대됨', active:'진행 중', completed:'완료', report_ready:'리포트 준비됨', failed:'실패'};
export const actorLabel: Record<string,string> = {candidate:'나', colleague:'이도현 · AI 동료', leader:'한서윤 · 팀장', assistant:'AI 채팅',system:'시스템',company:'회사 문서'};
export const ownerLabel: Record<string,string> = {candidate:'지원자',colleague:'이도현',shared:'함께'};
export function credential(sid:string) { return sessionStorage.getItem('candidate:'+sid)||''; }
export function clock(seconds:number) { return Math.max(0,Math.floor(seconds/60)).toString().padStart(2,'0')+':'+Math.max(0,Math.floor(seconds%60)).toString().padStart(2,'0'); }
export type Part = {id:string;number:number;title:string;body:string;owner:string;status:string;version:number};
export type Entry = {id:string;kind:string;actor:string;at:number;elapsed:number;data:Record<string,any>};

export const aiErrorLabel: Record<string,string> = {cli_rate_limited:'AI 요청이 일시적으로 제한되었습니다. 잠시 후 다시 요청해 주세요.',model_timeout:'응답 시간이 초과되었습니다. 잠시 후 다시 요청해 주세요.',model_budget_exhausted:'이 요청의 처리 한도에 도달했습니다. 요청을 나누어 다시 보내 주세요.'};
export const reportActorLabel: Record<string,string> = {...actorLabel,candidate:'지원자'};
export function elapsedClock(seconds:number) {const value=Math.max(0,Math.floor(seconds));return Math.floor(value/3600).toString().padStart(2,'0')+':'+Math.floor(value%3600/60).toString().padStart(2,'0')+':'+(value%60).toString().padStart(2,'0');}
