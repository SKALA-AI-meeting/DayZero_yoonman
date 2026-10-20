import CompanyPortal from '@/components/company-portal';
export default async function ReportPage({params}:{params:Promise<{sessionId:string}>}){
 const {sessionId}=await params;
 return <CompanyPortal key={sessionId} sessionId={sessionId}/>;
}
