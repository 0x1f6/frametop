// Frame controller buttons for the relay's button mappings (SteamVR input, see ft-pointer.cpp).
//
// The Frame controllers aren't input devices on the host (no evdev node, no hidraw): only
// SteamVR sees them. So the helper, an overlay client anyway, reads them with SteamVR input
// and sends each press and release to the relay ("vrbtn <hand>/<button> 1|0" on
// @frametop_relay), which does the mapped action, like for a mouse button.
//
// actions/ft_pointer_actions.json has one action set per button, each with one boolean action
// bound to that button's click (actions/bindings_frame_controller.json). Only the sets of
// mapped buttons are active: the relay says which ("vrbind <button>..."; "vrbind *" for all,
// while the settings app captures a button; "vrbind -" for none), and asks again with
// "vrhello" when the helper connects. While a game runs (a scene application), the mapped
// buttons are left to it, unless the list starts with "+games"; capturing takes them anyway. The sets are active at an overlay-global priority, so
// SteamVR delivers the buttons without the helper having input focus, and takes those
// buttons (only those) from the game: a mapped button is Frametop's. That needs SteamVR's
// "Enable global input from overlays (Experimental)" (steamvr/globalActionSetPriority);
// "vrglobal on|off" sets it, "vrstatus" replies with the state as JSON.
#pragma once

#include <openvr.h>

#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

class ControllerButtons {
  public:
    static constexpr const char *kButtons[] = {
        "left/view",   "left/dpad_up", "left/dpad_down", "left/dpad_left", "left/dpad_right", "left/bumper",
        "left/trigger", "left/grip",   "left/thumbstick", "right/menu",    "right/a",         "right/b",
        "right/x",     "right/y",      "right/bumper",   "right/trigger",  "right/grip",      "right/thumbstick"};
    static constexpr int kCount = sizeof kButtons / sizeof kButtons[0];
    // Above SteamVR's own overlay input, inside the overlay-global range.
    static constexpr int32_t kPriority = vr::k_nActionSetOverlayGlobalPriorityMin + 0x100;

    // After VR_Init. The manifest can only be set once per connection.
    void Init(const std::string &manifest) {
        input_ = vr::VRInput();
        const vr::EVRInputError e = input_->SetActionManifestPath(manifest.c_str());
        ok_ = e == vr::VRInputError_None;
        std::printf("controller buttons: action manifest %s: %s\n", manifest.c_str(), ok_ ? "ok" : "error");
        if (!ok_) std::printf("controller buttons: SetActionManifestPath error %d\n", int(e));
        for (int i = 0; i < kCount; ++i) {
            std::string name = kButtons[i];
            name[name.find('/')] = '_';
            input_->GetActionSetHandle(("/actions/" + name).c_str(), &set_[i]);
            input_->GetActionHandle(("/actions/" + name + "/in/press").c_str(), &action_[i]);
        }
        std::fflush(stdout);
    }

    // "vrbind" arguments: [+games] then button names, "*" (all) or "-" (none). Returns what's
    // bound now.
    std::string Bind(const char *args) {
        bool want[kCount] = {};
        char word[64];
        int used = 0;
        games_ = capture_ = false;
        while (std::sscanf(args, " %63s%n", word, &used) == 1) {
            args += used;
            if (std::strcmp(word, "+games") == 0) games_ = true;
            if (std::strcmp(word, "*") == 0) capture_ = true;
            for (int i = 0; i < kCount; ++i)
                if (std::strcmp(word, "*") == 0 || std::strcmp(word, kButtons[i]) == 0) want[i] = true;
        }
        for (int i = 0; i < kCount; ++i) bound_[i] = want[i];
        return Bound();
    }

    std::string Bound() const {
        std::string s;
        for (int i = 0; i < kCount; ++i)
            if (bound_[i]) s += (s.empty() ? "" : " ") + std::string(kButtons[i]);
        return (s.empty() ? "-" : s) + (games_ ? " (in games too)" : " (outside games)");
    }

    // Every frame. send(button, pressed) for each change; a button that's unbound (or can't
    // be read any more) while down is released, so no action stays held. inGame: a scene
    // application is running.
    template <class Send> void Poll(Send send, bool inGame) {
        if (!ok_) return;
        inGame_ = inGame;
        const bool take = !inGame || games_ || capture_;
        std::vector<vr::VRActiveActionSet_t> sets;
        for (int i = 0; i < kCount; ++i)
            if (bound_[i] && take) {
                vr::VRActiveActionSet_t s{};
                s.ulActionSet = set_[i];
                s.nPriority = kPriority;
                sets.push_back(s);
            }
        const bool updated =
            !sets.empty() && input_->UpdateActionState(sets.data(), sizeof sets[0], uint32_t(sets.size())) ==
                                 vr::VRInputError_None;
        for (int i = 0; i < kCount; ++i) {
            bool down = false;
            active_[i] = false;
            if (bound_[i] && take && updated) {
                vr::InputDigitalActionData_t d{};
                if (input_->GetDigitalActionData(action_[i], &d, sizeof d, vr::k_ulInvalidInputValueHandle) ==
                    vr::VRInputError_None) {
                    active_[i] = d.bActive;
                    down = d.bActive && d.bState;
                }
            }
            if (down != down_[i]) {
                down_[i] = down;
                send(kButtons[i], down);
            }
        }
    }

    // {"manifest":true,"global":false,"bound":[...],"active":[...]}: active = bound and
    // delivered (a controller that has the button is on, and SteamVR lets us have it).
    std::string Status() const {
        const bool global = vr::VRSettings()->GetBool("steamvr", "globalActionSetPriority", nullptr);
        std::string bound, active;
        for (int i = 0; i < kCount; ++i) {
            if (bound_[i]) bound += std::string(bound.empty() ? "" : ",") + "\"" + kButtons[i] + "\"";
            if (active_[i]) active += std::string(active.empty() ? "" : ",") + "\"" + kButtons[i] + "\"";
        }
        return std::string("{\"t\":\"vrstatus\",\"manifest\":") + (ok_ ? "true" : "false") +
               ",\"global\":" + (global ? "true" : "false") + ",\"in_game\":" + (inGame_ ? "true" : "false") +
               ",\"games\":" + (games_ ? "true" : "false") + ",\"bound\":[" + bound + "],\"active\":[" + active + "]}";
    }

    static void SetGlobal(bool on) {
        vr::VRSettings()->SetBool("steamvr", "globalActionSetPriority", on);
        std::printf("controller buttons: SteamVR global input from overlays %s\n", on ? "on" : "off");
        std::fflush(stdout);
    }

  private:
    vr::IVRInput *input_ = nullptr;
    bool ok_ = false;
    vr::VRActionSetHandle_t set_[kCount] = {};
    vr::VRActionHandle_t action_[kCount] = {};
    bool bound_[kCount] = {}, down_[kCount] = {}, active_[kCount] = {};
    bool games_ = false, capture_ = false, inGame_ = false;
};
