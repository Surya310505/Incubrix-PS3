#!/bin/sh
set -eu

if [ "$#" -ne 1 ]; then
    echo "Usage: $0 /path/outside/project/submission.zip" >&2
    exit 2
fi

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
project_root=$(dirname -- "$script_dir")
output_parent=$(dirname -- "$1")
output_name=$(basename -- "$1")

if [ ! -d "$output_parent" ]; then
    echo "Output directory does not exist: $output_parent" >&2
    exit 2
fi
output_parent=$(CDPATH= cd -- "$output_parent" && pwd)
archive_path="$output_parent/$output_name"

case "$archive_path" in
    "$project_root"/*)
        echo "Choose an archive path outside the project directory." >&2
        exit 2
        ;;
esac

if [ -e "$archive_path" ]; then
    echo "Refusing to overwrite existing archive: $archive_path" >&2
    exit 2
fi
if ! command -v zip >/dev/null 2>&1; then
    echo "The zip command is required to package the submission." >&2
    exit 127
fi

temporary_archive=$(mktemp "$archive_path.tmp.XXXXXX")
trap 'rm -f "$temporary_archive"' EXIT HUP INT TERM
rm -f "$temporary_archive"

cd "$project_root"
zip -q -r -X "$temporary_archive" . \
    -x \
    '.venv' '.venv/*' '*/.venv' '*/.venv/*' \
    '.pytest_cache' '.pytest_cache/*' '*/.pytest_cache' '*/.pytest_cache/*' \
    '__pycache__' '__pycache__/*' '*/__pycache__' '*/__pycache__/*' \
    '*.pyc' '*.pyo' '*.pyd' \
    '.DS_Store' '*/.DS_Store' '__MACOSX' '__MACOSX/*' '*/__MACOSX' '*/__MACOSX/*' \
    '.env' '.git' '.git/*' '*/.git' '*/.git/*' \
    'data' 'data/*' 'outputs' 'outputs/*'

mv "$temporary_archive" "$archive_path"
trap - EXIT HUP INT TERM
printf 'Created clean submission archive: %s\n' "$archive_path"
