[버그 리포트] Shiny 커스텀 탭 전환 시 output 렌더링 suspend 문제
환경

Shiny for Python
커스텀 JS 탭 전환 (switchTab)
Safari (iOS/macOS)

증상
설정 탭을 한 번이라도 방문한 후 계좌 탭으로 돌아와서 계좌 카드를 클릭하면 화면이 반투명하게 디밍되며 멈춤. 계좌 추가 버튼도 반응 없음. 하단 탭바만 살아있음. 다른 탭에서 계좌 탭으로 올 때는 정상.
원인
Shiny는 IntersectionObserver로 .shiny-bound-output 엘리먼트의 visibility를 감지해서 _hidden 상태를 서버에 전달한다. _hidden:true인 output은 서버가 렌더링을 suspend한다.
커스텀 탭 전환에서 display:none → display:block으로 전환할 때 Safari에서 IntersectionObserver가 자동으로 감지하지 못하는 경우가 있다. 이로 인해 _hidden:false가 서버로 전달되지 않아 렌더링이 suspend된 채로 유지된다.
recalculating 클래스가 붙은 채로 풀리지 않는 것이 클라이언트 측 증거이고, 서버 로그에 아무것도 찍히지 않는 것이 서버 측 증거다.
진단 방법

문제 발생 상태에서 #accounts-main_view에 recalculating 클래스가 붙어있는지 확인
WebSocket Messages에서 카드 클릭 후 서버→클라이언트 메시지가 없는지 확인
콘솔에서 수동 트리거 테스트:

javascriptdocument.querySelectorAll('#tab-accounts .shiny-bound-output').forEach(el => {
    const cb = $(el).data('shiny-intersection-observer-callback');
    if(cb) cb();
});
위 실행 후 정상 동작하면 이 버그가 원인.
해결
switchTab 함수에서 탭을 display:block으로 전환한 후 해당 탭 내 .shiny-bound-output 엘리먼트들의 IntersectionObserver 콜백을 수동으로 호출:
javascripttarget.querySelectorAll('.shiny-bound-output').forEach(function(output) {
    const cb = $(output).data('shiny-intersection-observer-callback');
    if(cb) cb();
});
참고

$(target).trigger('shown') 은 Shiny의 data-display-if 전용 메커니즘으로 이 문제에는 효과 없음
shiny.js의 isVisible, doSendHiddenState, ensureObservers 함수 참고
Chrome에서는 재현 안 될 수 있음 (Safari 특이 동작)