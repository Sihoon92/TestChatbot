// 모든 API 요청에 붙는 공통 헤더. 기본은 비어 있고, 인증 토큰 등 커스텀 헤더가
// 필요하면 여기에 추가한다(모든 fetch 호출이 이 객체를 스프레드한다).
export const API_HEADERS: Record<string, string> = {};
