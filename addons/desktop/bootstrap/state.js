window.showState = (message) => {
  document.getElementById('message').textContent = message;
  document.querySelector('.hint').textContent = '可关闭窗口退出。排查信息保存在本机日志中。';
};
if (new URLSearchParams(location.search).has('maintenance')) {
  window.showState('Alice 已暂停。请在“数据与更新 → 备份与恢复”中继续启动或恢复数据。');
}
