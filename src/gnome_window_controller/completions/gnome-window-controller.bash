# Bash completion for gnome-window-controller.
#
# Install: copy this file into a bash-completion directory, named after the command so that
# bash-completion loads it lazily:
#
#   mkdir -p ~/.local/share/bash-completion/completions
#   cp gnome-window-controller.bash \
#      ~/.local/share/bash-completion/completions/gnome-window-controller
#
# Or, to try it out in the current shell only:
#
#   . gnome-window-controller.bash
#
# @author eduardotc
# @email eduardotcampos@hotmail.com

_gwc_opts='
--list-windows --details-windows --list-monitors --color --json
--focus --scope --workspace --exclude --chfocus
--highlight --highlight-mode --highlight-color --highlight-width --highlight-radius
--highlight-inset --highlight-duration --show-focus --no-highlight
--version --help
'

_gwc_chfocus_primary='monitor win right left up down last'
_gwc_highlight_actions='status install uninstall on off flash clear'

# Second --chfocus word, keyed by the first one. right/left/last take none.
_gwc_chfocus_secondary() {
    case $1 in
        monitor) echo 'top bottom' ;;
        win) echo 'same_app same_monitor same_monitor_down same_monitor_up' ;;
    esac
}

# Window classes of the currently open windows, for --focus.
#
# Titles are deliberately left out: they change constantly and usually contain spaces, which a
# completion cannot insert without quoting. A wm_class is a single stable token, which is what
# --focus is normally given anyway.
_gwc_window_names() {
    local out
    out=$("$1" --list-windows --json --color never 2>/dev/null) || return 0
    printf '%s\n' "$out" | python3 -c '
import json, sys

try:
    windows = json.load(sys.stdin)
except (ValueError, TypeError):
    sys.exit(0)

names = {
    str(w[key])
    for w in windows
    for key in ("wm_class", "wm_class_instance")
    if isinstance(w, dict) and w.get(key)
}
print("\n".join(sorted(n for n in names if " " not in n)))
' 2>/dev/null
}

_gnome_window_controller() {
    local cur prev cmd opt argi i
    cur=${COMP_WORDS[COMP_CWORD]}
    prev=${COMP_WORDS[COMP_CWORD - 1]}
    cmd=${COMP_WORDS[0]}

    # `--color=<TAB>` splits into `--color` `=` `` because COMP_WORDBREAKS contains `=`.
    if [[ $cur == '=' ]]; then
        cur=''
        prev=${COMP_WORDS[COMP_CWORD - 2]}
    elif [[ $prev == '=' ]]; then
        prev=${COMP_WORDS[COMP_CWORD - 2]}
    fi

    # Find the most recent option and how many words already follow it, so that a value is
    # completed for the flag it belongs to rather than for whatever happens to sit just behind.
    opt=''
    argi=0
    for ((i = COMP_CWORD - 1; i > 0; i--)); do
        [[ ${COMP_WORDS[i]} == '=' ]] && continue
        if [[ ${COMP_WORDS[i]} == --* ]]; then
            opt=${COMP_WORDS[i]}
            break
        fi
        ((argi++))
    done
    ((argi++))  # the word being completed is the argi-th after `opt`

    case $opt in
        --focus | --exclude)
            mapfile -t COMPREPLY < <(compgen -W "$(_gwc_window_names "$cmd")" -- "$cur")
            return 0
            ;;
        --chfocus)
            case $argi in
                1)
                    mapfile -t COMPREPLY < <(compgen -W "$_gwc_chfocus_primary" -- "$cur")
                    return 0
                    ;;
                2)
                    # right/left/last take no second word; fall through to the option list.
                    local secondary
                    secondary=$(_gwc_chfocus_secondary "$prev")
                    if [[ -n $secondary ]]; then
                        mapfile -t COMPREPLY < <(compgen -W "$secondary" -- "$cur")
                        return 0
                    fi
                    ;;
            esac
            ;;
        --color)
            [[ $argi == 1 ]] && {
                mapfile -t COMPREPLY < <(compgen -W 'auto always never' -- "$cur")
                return 0
            }
            ;;
        --scope)
            [[ $argi == 1 ]] && {
                mapfile -t COMPREPLY < <(compgen -W 'any current-monitor other-monitor' -- "$cur")
                return 0
            }
            ;;
        --workspace)
            [[ $argi == 1 ]] && {
                mapfile -t COMPREPLY < <(compgen -W 'current prefer-current any' -- "$cur")
                return 0
            }
            ;;
        --highlight)
            [[ $argi == 1 ]] && {
                mapfile -t COMPREPLY < <(compgen -W "$_gwc_highlight_actions" -- "$cur")
                return 0
            }
            ;;
        --highlight-mode)
            [[ $argi == 1 ]] && {
                mapfile -t COMPREPLY < <(compgen -W 'always commands off' -- "$cur")
                return 0
            }
            ;;
        --highlight-color | --highlight-width | --highlight-radius | --highlight-inset | \
        --highlight-duration)
            # A free-form color or a number; nothing sensible to offer.
            [[ $argi == 1 ]] && return 0
            ;;
    esac

    mapfile -t COMPREPLY < <(compgen -W "$_gwc_opts" -- "$cur")
    return 0
}

complete -F _gnome_window_controller gnome-window-controller gnome_window_controller
