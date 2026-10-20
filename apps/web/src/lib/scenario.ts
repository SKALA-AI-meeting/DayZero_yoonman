export type WorkScenario = {
 id:string;title:string;company:string;team:string;service:string;introduction:string;
 brief:string;short_brief:string;deliverable_title:string;deliverable_description:string;
 completion_conditions:string[];duration_seconds:number;workflow:string;
 roles:Record<'leader'|'colleague',{name:string;role:string;introduction:string}>;
 sections:{title:string;owner:string}[];documents:{title:string;available:boolean}[];
};

// Compatibility for sessions served by the previous backend during a running verification.
const legacy = {
  "id": "looplab-v1",
  "title": "모아런 신규 기능 기획",
  "company": "루프랩",
  "team": "프로덕트 팀",
  "service": "모아런",
  "introduction": "루프랩은 러닝 모임 앱 ‘모아런’을 만드는 12명 리모트 팀입니다.",
  "brief": "첫 모임에 나온 사용자가 두 번째 모임에도 다시 나오게 만드는 신규 기능을 기획해 주세요. 2시간 뒤 2~3쪽 기획안을 공유 문서로 받겠습니다. 필요하면 AI 채팅을 써도 됩니다.",
  "short_brief": "첫 모임에 나온 사용자가 두 번째 모임에도 다시 나오게 만드는 신규 기능을 기획합니다.",
  "deliverable_title": "모아런 신규 기능 기획안",
  "deliverable_description": "2~3쪽 분량의 공유 기획안",
  "completion_conditions": [
    "제공된 자료를 근거로 대상 사용자와 문제를 설명합니다.",
    "회사 정책과 변경된 요구사항을 결과물에 반영합니다.",
    "제안의 실행 단계와 확인할 지표를 정리합니다."
  ],
  "roles": {
    "leader": {
      "name": "한서윤",
      "role": "프로덕트 팀장",
      "introduction": "과제 안내와 진행 보고를 함께합니다."
    },
    "colleague": {
      "name": "이도현",
      "role": "기획 동료",
      "introduction": "안녕하세요. 기획자 이도현입니다. 함께 기획안을 완성해 봐요."
    }
  },
  "sections": [
    {
      "title": "문제 정의와 대상 사용자",
      "owner": "candidate"
    },
    {
      "title": "사용자 인터뷰에서 찾은 근거",
      "owner": "candidate"
    },
    {
      "title": "핵심 기능 제안",
      "owner": "shared"
    },
    {
      "title": "비슷한 서비스 사례",
      "owner": "colleague"
    },
    {
      "title": "성공 지표와 출시 일정",
      "owner": "colleague"
    }
  ],
  "duration_seconds": 7200,
  "workflow": "legacy",
  "documents": [
    {
      "title": "사용자 인터뷰 요약",
      "release_after": 3900
    },
    {
      "title": "회사·서비스 소개",
      "release_after": 0
    },
    {
      "title": "지표 현황",
      "release_after": 0
    },
    {
      "title": "지난주 회의록",
      "release_after": 0
    },
    {
      "title": "운영 정책",
      "release_after": 0
    }
  ]
};

export function workScenario(workspace:any):WorkScenario {
 if(workspace?.scenario)return workspace.scenario;
 const elapsed=7200-(workspace?.remaining??7200);
 return {...legacy,documents:legacy.documents.map(d=>({title:d.title,available:d.release_after<=elapsed}))} as WorkScenario;
}
export function scenarioLabels(scenario:WorkScenario){
 return {
  actors:{candidate:'나',leader:scenario.roles.leader.name+' · '+scenario.roles.leader.role,
   colleague:scenario.roles.colleague.name+' · '+scenario.roles.colleague.role,
   assistant:'개인 AI',system:'시스템',company:'회사 문서'} as Record<string,string>,
  owners:{candidate:'나',colleague:scenario.roles.colleague.name,shared:'함께'} as Record<string,string>
 };
}
