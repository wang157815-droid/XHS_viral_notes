#!/bin/bash
#
# 历史记录清理脚本
# 用于清理缓存、日志、分析结果等历史数据
#

set -e

# 获取脚本所在目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 帮助信息
show_help() {
    echo -e "${BLUE}=== 小红书爆文分析系统 - 历史记录清理工具 ===${NC}"
    echo ""
    echo "用法: $0 [选项] [类别...]"
    echo ""
    echo "选项:"
    echo "  -h, --help      显示帮助信息"
    echo "  -l, --list      列出所有类别及大小"
    echo "  -a, --all       清理所有类别"
    echo "  -d, --dry-run   仅预览，不实际删除"
    echo "  -f, --force     强制删除，不询问确认"
    echo ""
    echo "类别:"
    echo "  cover_cache     封面缓存"
    echo "  video_cache     视频缓存"
    echo "  viral_analysis  分析结果"
    echo "  excel_datas     爬虫数据"
    echo "  media_datas     媒体文件"
    echo "  logs            日志文件"
    echo "  chromadb        向量数据库"
    echo "  av_sync_cache   音画同步缓存"
    echo ""
    echo "示例:"
    echo "  $0 -l                         # 列出所有类别"
    echo "  $0 cover_cache video_cache    # 清理封面和视频缓存"
    echo "  $0 -d -a                      # 预览清理所有"
    echo "  $0 -f -a                      # 强制清理所有"
    echo ""
}

# 列出所有类别
list_categories() {
    echo -e "${BLUE}=== 清理类别列表 ===${NC}"
    echo ""

    cd "$PROJECT_ROOT"

    python3 -c "
from viral_agent.services.cleanup_service import CleanupService

service = CleanupService()
info = service.get_category_info()

total = 0
print(f'{'类别':<15} {'名称':<12} {'文件数':>8} {'大小(MB)':>10} {'状态':<8}')
print('-' * 60)

for key, cat in info.items():
    status = '✓ 存在' if cat['exists'] else '- 空'
    print(f'{key:<15} {cat[\"name\"]:<12} {cat[\"file_count\"]:>8} {cat[\"size_mb\"]:>10.2f} {status:<8}')
    total += cat['size_mb']

print('-' * 60)
print(f'{'总计':<28} {sum(c[\"file_count\"] for c in info.values()):>8} {total:>10.2f}')
"
    echo ""
}

# 执行清理
do_cleanup() {
    local dry_run=$1
    shift
    local categories=("$@")

    cd "$PROJECT_ROOT"

    if [ "$dry_run" = "true" ]; then
        echo -e "${YELLOW}[预览模式] 以下内容将被清理:${NC}"
    else
        echo -e "${RED}[执行清理] 正在删除以下内容:${NC}"
    fi
    echo ""

    # 构建 Python 命令
    local cats_str=$(printf "'%s'," "${categories[@]}")
    cats_str="[${cats_str%,}]"

    python3 -c "
from viral_agent.services.cleanup_service import CleanupService

service = CleanupService()
categories = $cats_str
dry_run = $( [ "$dry_run" = "true" ] && echo "True" || echo "False" )

summary = service.cleanup_all(categories=categories, dry_run=dry_run)

for result in summary.results:
    if result.success:
        status = '✓' if result.files_deleted > 0 else '-'
        print(f'{status} {result.category}: {result.files_deleted} 个文件, {result.size_freed_mb:.2f} MB')
    else:
        print(f'✗ {result.category}: {result.error}')

print()
print(f'总计: {summary.total_files} 个文件, {summary.total_size_mb:.2f} MB')
"

    if [ "$dry_run" = "true" ]; then
        echo ""
        echo -e "${YELLOW}这是预览模式，未实际删除。使用不带 -d 参数执行实际删除。${NC}"
    else
        echo ""
        echo -e "${GREEN}清理完成！${NC}"
    fi
}

# 主逻辑
main() {
    local dry_run=false
    local force=false
    local list_only=false
    local all_categories=false
    local categories=()

    # 解析参数
    while [[ $# -gt 0 ]]; do
        case $1 in
            -h|--help)
                show_help
                exit 0
                ;;
            -l|--list)
                list_only=true
                shift
                ;;
            -a|--all)
                all_categories=true
                shift
                ;;
            -d|--dry-run)
                dry_run=true
                shift
                ;;
            -f|--force)
                force=true
                shift
                ;;
            *)
                categories+=("$1")
                shift
                ;;
        esac
    done

    # 如果没有参数，显示帮助
    if [ ${#categories[@]} -eq 0 ] && [ "$list_only" = false ] && [ "$all_categories" = false ]; then
        show_help
        exit 0
    fi

    # 列出类别
    if [ "$list_only" = true ]; then
        list_categories
        exit 0
    fi

    # 确定要清理的类别
    if [ "$all_categories" = true ]; then
        categories=(cover_cache video_cache viral_analysis excel_datas media_datas logs chromadb av_sync_cache)
    fi

    # 确认操作
    if [ "$dry_run" = false ] && [ "$force" = false ]; then
        echo -e "${YELLOW}警告: 即将删除以下类别的数据:${NC}"
        for cat in "${categories[@]}"; do
            echo "  - $cat"
        done
        echo ""
        read -p "确认删除? (y/N) " confirm
        if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
            echo "已取消"
            exit 0
        fi
    fi

    # 执行清理
    do_cleanup "$dry_run" "${categories[@]}"
}

main "$@"
