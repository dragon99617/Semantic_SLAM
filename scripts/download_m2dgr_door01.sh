#!/usr/bin/env bash
# M2DGR door_01 下载器 v2：走 SharePoint shares API（返回 JSON 状态 + Retry-After），
# 遵守 Retry-After 退避，Range 断点续传，下完校验 ROS1 bag 头。
set -uo pipefail

DEST="${DEST:-$HOME/SLAM_project/datasets/M2DGR/door_01}"
MAX_HOURS="${MAX_HOURS:-6}"
UA='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36'
SHARE_BAG='https://sjtueducn-my.sharepoint.com/:u:/g/personal/594666_sjtu_edu_cn/ERxIk8o_HwlAgbqJ2wwgHl8Br3uBZhyuBbxM2bG_0A6QYA?e=uQr94R'
SHARE_GT='https://sjtueducn-my.sharepoint.com/:t:/g/personal/594666_sjtu_edu_cn/EVgYKmnhIy9AhCC1tOOKUOYB91dEx3mGmFQ9ewLtlNDw6g?e=uylMro'

mkdir -p "$DEST"; LOG="$DEST/download.log"
log() { printf '[%s] %s\n' "$(date -Is)" "$*" | tee -a "$LOG"; }

# share link -> _api/v2.1/shares/u!<base64url(urlencoded)>
share_token() {
  python3 - "$1" <<'PY'
import base64,sys,urllib.parse
print('u!'+base64.urlsafe_b64encode(urllib.parse.quote(sys.argv[1],safe='').encode()).decode().rstrip('='))
PY
}
BAG_TOK="$(share_token "$SHARE_BAG")"
GT_TOK="$(share_token "$SHARE_GT")"
API='https://sjtueducn-my.sharepoint.com/_api/v2.1/shares'
BAG_API="$API/$BAG_TOK/driveItem"
GT_API="$API/$GT_TOK/driveItem"

# 探测：输出 READY / WAIT <secs> / FATAL <desc>
probe() {
  local url="$1" hdr code retry
  hdr="$(curl -sS -o /dev/null -D - -m 60 -A "$UA" -H 'Accept: application/json' "$url?\$select=name,size" 2>&1 | tr -d '\r')"
  code="$(printf '%s\n' "$hdr" | awk 'NR==1{print $2}')"
  retry="$(printf '%s\n' "$hdr" | awk 'tolower($1)=="retry-after:"{print $2}')"
  case "$code" in
    200) echo "READY" ;;
    429) echo "WAIT ${retry:-600}" ;;
    404|410) echo "FATAL not-found($code)" ;;
    *)   echo "WAIT 600 ($code)" ;;
  esac
}
size_of() { curl -sS -m 60 -A "$UA" -H 'Accept: application/json' "$1?\$select=size" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("size",0))' 2>/dev/null || echo 0; }

log "=== door_01 下载任务 v2 启动（shares API），上限 ${MAX_HOURS}h ==="
deadline=$(( $(date +%s) + MAX_HOURS*3600 ))
while [ "$(date +%s)" -lt "$deadline" ]; do
  st="$(probe "$BAG_API")"
  log "探测：$st"
  case "$st" in
    READY)
      total="$(size_of "$BAG_API")"
      log "源可用，目标大小 ${total}B"
      curl -L -C - --retry 10 --retry-delay 20 --retry-all-errors -m 172800 \
           -A "$UA" -o "$DEST/door_01.bag.part" "$BAG_API/content" \
           && { magic="$(head -c 13 "$DEST/door_01.bag.part")"
                if [ "$magic" = "#ROSBAG V2.0" ]; then
                  mv "$DEST/door_01.bag.part" "$DEST/door_01.bag"; log "bag 校验通过"
                  if curl -L -C - -m 3600 -A "$UA" -o "$DEST/door_01_gt.txt.part" "$GT_API/content"; then
                    mv "$DEST/door_01_gt.txt.part" "$DEST/door_01_gt.txt"
                    sha256sum "$DEST/door_01.bag" "$DEST/door_01_gt.txt" > "$DEST/SHA256SUMS"
                    log "完成"; ls -lh "$DEST"; exit 0
                  fi
                else log "magic 不符：[$magic]，保留 .part"; fi; }
      ;;
    WAIT*)
      s="${st#WAIT }"; s="${s%% *}"
      log "限流，按 Retry-After 休眠 ${s}s"
      sleep "$s"; continue ;;
    FATAL*) log "不可恢复：$st"; exit 3 ;;
  esac
  sleep 60
done
log "超时未取得数据"
exit 2