#!/usr/bin/env bash
# ===== Resource Hub VPN/代理 通用模板(source 到当前 shell 即可生效)=====
# 适配晨涧云学术代理 proxy.mornai.cn:7890;换其他代理只改下面一行 URL。
# 用法: source proxy_template.sh   或   . ./proxy_template.sh
# 只对「资源获取链路」(search/pages/delivery/browser)生效;
# LLM API 与 Webhook 回调保持直连(不建议全局代理)。

export RH_PROXY_URL="${RH_PROXY_URL:-http://proxy.mornai.cn:7890}"
# 标准变量(requests/curl/wget/git 原生识别,与本模板的 RH_PROXY_URL 等价)
export http_proxy="${http_proxy:-$RH_PROXY_URL}"
export https_proxy="${https_proxy:-$RH_PROXY_URL}"
export no_proxy="${no_proxy:-localhost,127.0.0.1,::1}"

echo "[proxy] 已启用: $RH_PROXY_URL"
echo "[proxy] no_proxy: $no_proxy"
echo "[proxy] 关闭: unset RH_PROXY_URL http_proxy https_proxy"
