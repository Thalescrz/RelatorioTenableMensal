#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
bundle_root="$(cd -- "$script_dir/.." && pwd -P)"
install_root="$bundle_root"
what_if=0
linger_user=""

while (($#)); do
  case "$1" in
    --install-root)
      install_root="${2:?INSTALL_ROOT_REQUIRED}"
      shift 2
      ;;
    --what-if)
      what_if=1
      shift
      ;;
    --enable-linger-for)
      linger_user="${2:?LINGER_USER_REQUIRED}"
      shift 2
      ;;
    *)
      printf 'UNKNOWN_ARGUMENT: %s\n' "$1" >&2
      exit 2
      ;;
  esac
done

if ((what_if)); then
  printf 'OFFLINE_SETUP_PLAN platform=linux-x86_64 source=%s target=%s\n' "$bundle_root" "$install_root"
  exit 0
fi
if ((EUID != 0)); then
  printf 'SETUP_REQUIRES_ELEVATION\n' >&2
  exit 1
fi
if [[ ! -f "$bundle_root/bundle-manifest.json" || ! -f "$bundle_root/bundle-manifest.sha256" ]]; then
  printf 'BUNDLE_MANIFEST_INVALID\n' >&2
  exit 1
fi
(
  cd -- "$bundle_root"
  sha256sum --check --quiet bundle-manifest.sha256
)

mkdir -p -- "$install_root"
if [[ "$(cd -- "$install_root" && pwd -P)" != "$bundle_root" ]]; then
  cp -a -- "$bundle_root/." "$install_root/"
  (
    cd -- "$install_root"
    sha256sum --check --quiet bundle-manifest.sha256
  )
fi

project_root="$(cd -- "$install_root" && pwd -P)"
state_root="$project_root/.install-state"
runtime_root="$project_root/.runtime"
build_root="$runtime_root/build"
python_root="$runtime_root/python"
python_bin="$python_root/bin/python3.14"
venv_python="$project_root/.venv/bin/python"
mkdir -p -- "$state_root" "$build_root" "$project_root/data" "$project_root/analysis_artifacts" "$project_root/outputs" "$project_root/credentials" "$project_root/orchestration"

if [[ ! -f "$state_root/native-dependencies.ok" ]]; then
  rm -rf -- "$build_root/native"
  mkdir -p -- "$build_root/native"
  tar -xf "$project_root/vendor/native/ubuntu-24.04-x86_64-build-deps.tar" -C "$build_root/native"
  mapfile -d '' native_packages < <(find "$build_root/native" -type f -name '*.deb' -print0)
  ((${#native_packages[@]})) || { printf 'NATIVE_PACKAGES_MISSING\n' >&2; exit 1; }
  dpkg -i -- "${native_packages[@]}"
  touch -- "$state_root/native-dependencies.ok"
fi

if [[ ! -f "$state_root/python-3.14.5.ok" ]]; then
  rm -rf -- "$build_root/python"
  mkdir -p -- "$build_root/python"
  tar -xf "$project_root/vendor/python/Python-3.14.5.tar.xz" -C "$build_root/python"
  python_source="$build_root/python/Python-3.14.5"
  (
    cd -- "$python_source"
    ./configure --prefix="$python_root" --with-ensurepip=install
    make -j"$(nproc)"
    make install
  )
  [[ -x "$python_bin" ]] || { printf 'PYTHON_INSTALL_FAILED\n' >&2; exit 1; }
  touch -- "$state_root/python-3.14.5.ok"
fi

if [[ ! -f "$state_root/libreoffice-26.2.4.2.ok" ]]; then
  rm -rf -- "$build_root/libreoffice"
  mkdir -p -- "$build_root/libreoffice"
  tar -xf "$project_root/vendor/libreoffice/LibreOffice_26.2.4.2_Linux_x86-64_deb.tar.gz" -C "$build_root/libreoffice"
  mapfile -d '' office_packages < <(find "$build_root/libreoffice" -type f -name '*.deb' -print0)
  ((${#office_packages[@]})) || { printf 'LIBREOFFICE_PACKAGES_MISSING\n' >&2; exit 1; }
  dpkg -i -- "${office_packages[@]}"
  [[ -x /opt/libreoffice26.2/program/soffice ]] || { printf 'LIBREOFFICE_INSTALL_FAILED\n' >&2; exit 1; }
  ln -sfn -- /opt/libreoffice26.2/program/soffice /usr/local/bin/soffice
  touch -- "$state_root/libreoffice-26.2.4.2.ok"
fi

if [[ ! -f "$state_root/postgresql-18.ok" ]]; then
  rm -rf -- "$build_root/postgresql"
  mkdir -p -- "$build_root/postgresql"
  tar -xf "$project_root/vendor/postgresql/postgresql-18-offline-debs.tar" -C "$build_root/postgresql"
  mapfile -d '' postgres_packages < <(find "$build_root/postgresql" -type f -name '*.deb' -print0)
  ((${#postgres_packages[@]})) || { printf 'POSTGRESQL_PACKAGES_MISSING\n' >&2; exit 1; }
  dpkg -i -- "${postgres_packages[@]}"
  systemctl enable --now postgresql
  touch -- "$state_root/postgresql-18.ok"
fi

if [[ ! -f "$state_root/fonts-linux-carlito-v1.ok" ]]; then
  font_root=/usr/local/share/fonts/tenable-reports
  install -d -m 0755 -- "$font_root"
  install -m 0644 -- "$project_root/vendor/fonts/linux/"*.ttf "$font_root/"
  fc-cache -f "$font_root"
  touch -- "$state_root/fonts-linux-carlito-v1.ok"
fi

if [[ ! -x "$venv_python" ]]; then
  "$python_bin" -m venv "$project_root/.venv"
fi
"$venv_python" -m pip install --no-index --find-links "$project_root/vendor/wheelhouse" --require-hashes -r "$project_root/requirements/lock/linux-py314.txt"
mapfile -t application_wheels < <(find "$project_root/vendor/application" -maxdepth 1 -type f -name 'tenable_reports-*.whl' -print)
((${#application_wheels[@]} == 1)) || { printf 'APPLICATION_WHEEL_INVALID\n' >&2; exit 1; }
"$venv_python" -m pip install --no-index --no-deps --force-reinstall "${application_wheels[0]}"

for name in clients document-control; do
  target="$project_root/orchestration/$name.json"
  [[ -f "$target" ]] || cp -- "$project_root/orchestration/$name.example.json" "$target"
done

if [[ ! -f "$state_root/database-bootstrap.ok" ]]; then
  "$project_root/scripts/bootstrap_postgresql.sh"
  touch -- "$state_root/database-bootstrap.ok"
fi

"$venv_python" -m tenable_reports environment-status --json --root "$project_root" --database-env-file "$project_root/credentials/database.env" --persistent-root "$project_root/data" --libreoffice /opt/libreoffice26.2/program/soffice
if [[ -n "$linger_user" ]]; then
  id "$linger_user" >/dev/null 2>&1 || { printf 'LINGER_USER_NOT_FOUND\n' >&2; exit 1; }
  loginctl enable-linger -- "$linger_user"
fi
printf 'OFFLINE_SETUP_COMPLETE platform=linux-x86_64 root=%s\n' "$project_root"
