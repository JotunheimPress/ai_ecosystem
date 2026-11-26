# Host profile: which GPU this machine has and how the backends use it.
# Sourced (never executed) by `stack` and every backend start script, so the NVIDIA
# workstation and the AMD Halo box (HALO_PLAN.md) run the same recipes.
#
# Settings come from ai-stack/host.env (git-ignored), PARSED, never sourced — same rule
# as koboldcpp/model.env. Every key is optional; without the file the GPU is detected.
#
#   GPU=nvidia|amd|none        the GPU the containers get
#   LLM_BACKEND=cuda|vulkan|rocm   how KoboldCPP uses it
#   BIND=127.0.0.1             publish the stack's open ports on this address only
#                              (unset = all interfaces, as on the workstation)
#   COMFYUI_PORT=8189          host port for ComfyUI (default 8188). On ai-net it is
#                              always comfyui:8188; this moves only the host side, e.g.
#                              off a port the OS already uses (AMD Rex ships its own
#                              ComfyUI on 127.0.0.1:8188).
#
# After host_profile_load:
#   HOST_GPU HOST_LLM_BACKEND HOST_BIND HOST_COMFY_PORT   the resolved values
#   gpu_args <nvidia-extra...>            sets the array GPU_ARGS for `podman run`
#   publish <host-port> <container-port>  prints a -p value honouring BIND
#   kobold_image / comfy_image            image tag for this profile
#   kobold_gpu_flag                       --usecublas (cuda, rocm) or --usevulkan

# Directory of this file, resolving symlinks: koboldcpp/start.sh is reached through
# a link farm in $ECO/koboldcpp, and its own dirname is not the checkout.
_HOST_LIB_DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"
HOST_ENV_FILE="${HOST_ENV_FILE:-$(dirname "$_HOST_LIB_DIR")/host.env}"

_host_env_get() {   # strict KEY=value read, quotes tolerated, nothing executed
  [ -f "$HOST_ENV_FILE" ] || return 0
  # `|| true`: a key the file leaves out is normal, but grep then exits 1, and under
  # `set -eo pipefail` (the start scripts) that would kill the caller silently.
  { grep -E "^[[:space:]]*$1=" "$HOST_ENV_FILE" || true; } | tail -1 | cut -d= -f2- \
    | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' -e "s/^[\"']//" -e "s/[\"']$//"
}

host_detect_gpu() {
  # NVIDIA first: an NVIDIA workstation with an AMD CPU also has /dev/kfd (this one does),
  # so /dev/kfd alone does not mean the GPU is AMD.
  if command -v nvidia-ctk >/dev/null 2>&1 \
     && nvidia-ctk cdi list 2>/dev/null | grep -q "nvidia.com/gpu=all"; then
    echo nvidia
  elif [ -e /dev/kfd ] && [ -d /dev/dri ]; then
    echo amd
  else
    echo none
  fi
}

host_profile_load() {
  local v
  # Safe values first, so a caller that carries on after a bad host.env (`stack doctor`
  # does, to report it) doesn't trip over unset variables under `set -u`.
  HOST_GPU=none; HOST_LLM_BACKEND=vulkan; HOST_BIND=""; HOST_COMFY_PORT=8188
  v="${STACK_GPU:-$(_host_env_get GPU)}"
  case "$v" in nvidia|amd|none) HOST_GPU="$v" ;;
    "") HOST_GPU="$(host_detect_gpu)" ;;
    *) echo "host.env: GPU must be nvidia, amd or none, got '$v'" >&2; return 1 ;;
  esac
  v="${KOBOLDCPP_BACKEND:-$(_host_env_get LLM_BACKEND)}"
  case "$v" in cuda|vulkan|rocm) HOST_LLM_BACKEND="$v" ;;
    "") case "$HOST_GPU" in nvidia) HOST_LLM_BACKEND=cuda ;; *) HOST_LLM_BACKEND=vulkan ;; esac ;;
    *) echo "host.env: LLM_BACKEND must be cuda, vulkan or rocm, got '$v'" >&2; return 1 ;;
  esac
  if [ "$HOST_LLM_BACKEND" = cuda ] && [ "$HOST_GPU" != nvidia ]; then
    echo "host.env: LLM_BACKEND=cuda needs an NVIDIA GPU (GPU=$HOST_GPU)" >&2; return 1
  fi
  if [ "$HOST_LLM_BACKEND" = rocm ] && [ "$HOST_GPU" != amd ]; then
    echo "host.env: LLM_BACKEND=rocm needs an AMD GPU (GPU=$HOST_GPU)" >&2; return 1
  fi
  v="${STACK_BIND:-$(_host_env_get BIND)}"
  if [ -n "$v" ] && ! [[ "$v" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]]; then
    echo "host.env: BIND must be an IPv4 address, got '$v'" >&2; return 1
  fi
  HOST_BIND="$v"
  v="${COMFYUI_PORT:-$(_host_env_get COMFYUI_PORT)}"
  if [ -n "$v" ]; then
    if ! [[ "$v" =~ ^[0-9]{1,5}$ ]] || ((v < 1 || v > 65535)); then
      echo "host.env: COMFYUI_PORT must be a port number, got '$v'" >&2; return 1
    fi
    HOST_COMFY_PORT="$v"
  fi
  export HOST_GPU HOST_LLM_BACKEND HOST_BIND HOST_COMFY_PORT
}

# GPU_ARGS for `podman run`. Extra arguments are added only on NVIDIA, so each script keeps
# the flags it has always used there (--privileged, label=disable) byte for byte.
gpu_args() {
  GPU_ARGS=()
  case "$HOST_GPU" in
    nvidia) GPU_ARGS=(--device nvidia.com/gpu=all "$@") ;;
    # ROCm needs /dev/kfd (compute) and /dev/dri (render nodes); Vulkan needs /dev/dri.
    # Keeping the user's video/render membership in the rootless container is what grants
    # access to those nodes. That is `--group-add keep-groups`, but podman refuses it over
    # the socket ("not supported in remote mode"), which is how stack-ui runs `stack`.
    # The crun annotation behind it does the same and works locally and remotely.
    # Debian/Ubuntu confine with AppArmor, not SELinux, so label=disable is a no-op there
    # but keeps Fedora hosts working.
    amd) GPU_ARGS=(--device /dev/kfd --device /dev/dri
                   --annotation run.oci.keep_original_groups=1
                   --security-opt label=disable --security-opt seccomp=unconfined) ;;
    *) GPU_ARGS=() ;;
  esac
}

publish() { echo "${HOST_BIND:+$HOST_BIND:}$1:$2"; }

# GPU memory in use now, in MiB; nothing when it can't be read. NVIDIA: the host's
# nvidia-smi. AMD: amdgpu's sysfs counters, VRAM carve-out + GTT (the unified pool), as in
# koboldcpp/start.sh. Best-effort: `stack gpu-release` only uses it to say what was freed.
gpu_used_mib() {
  case "$HOST_GPU" in
    nvidia) nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits 2>/dev/null \
              | head -1 | tr -d ' ' || true ;;
    amd) local d f sum=0 n=0
         for d in /sys/class/drm/card*/device; do
           for f in "$d/mem_info_vram_used" "$d/mem_info_gtt_used"; do
             [ -r "$f" ] && { sum=$((sum + $(cat "$f"))); n=1; }
           done
         done
         ((n)) && echo $((sum / 1048576)) || true ;;
  esac
}

kobold_image() {
  case "$HOST_LLM_BACKEND" in
    cuda) echo "localhost/koboldcpp:latest" ;;     # the original tag, kept for the 4070
    *)    echo "localhost/koboldcpp:$HOST_LLM_BACKEND" ;;
  esac
}
kobold_containerfile() {   # relative to the koboldcpp recipe dir
  case "$HOST_LLM_BACKEND" in cuda) echo Containerfile ;; *) echo "Containerfile.$HOST_LLM_BACKEND" ;; esac
}
kobold_gpu_flag() {
  case "$HOST_LLM_BACKEND" in
    vulkan) echo --usevulkan ;;
    *)      echo --usecublas ;;     # rocm too: the hipBLAS build answers to --usecublas
  esac
}

# Fortuna (typed decisions, fortuna/): the official llama.cpp server image for this GPU runtime,
# pinned by digest at build b11146 (fortuna/Containerfile says why that build), plus our layer.
fortuna_base_image() {
  case "$HOST_LLM_BACKEND" in
    cuda) echo "ghcr.io/ggml-org/llama.cpp@sha256:19e8d129e2b66434692102f03c68fdb079827cff08a3fe0fdf0b4fef9cc9041b" ;;
    rocm) echo "ghcr.io/ggml-org/llama.cpp@sha256:a4d07a422c64467bd3f4177b2721ba1e0bf1af187618a265b71e9cef9e830639" ;;
    *)    echo "ghcr.io/ggml-org/llama.cpp@sha256:171430232fd85c662776f13a5c4924e54256ece130b85fa6fe3fe5b519cf7052" ;;
  esac
}
fortuna_image() { echo "localhost/fortuna:$HOST_LLM_BACKEND"; }

comfy_image() {
  case "$HOST_GPU" in amd) echo "localhost/comfyui:rocm" ;; *) echo "localhost/comfyui:latest" ;; esac
}
comfy_containerfile() {
  case "$HOST_GPU" in amd) echo Containerfile.rocm ;; *) echo Containerfile ;; esac
}
