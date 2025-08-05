# Python libraries
import os
import sys

# Apple libraries
import objc
from AppKit import *
from WebKit import *
from Quartz import *
from Foundation import NSObject, NSURL, NSURLRequest, NSDate, NSTimer

# Local libraries
from .constants import (
    APP_TITLE,
    CORNER_RADIUS,
    DRAG_AREA_HEIGHT,
    LOGO_BLACK_PATH,
    LOGO_WHITE_PATH,
    FRAME_SAVE_NAME,
    STATUS_ITEM_CONTEXT,
    WEBSITE,
)
from .launcher import (
    install_startup,
    uninstall_startup,
)
from .listener import (
    global_show_hide_listener,
    load_custom_launcher_trigger,
    set_custom_launcher_trigger,
)


# Custom window (contains entire application).
class AppWindow(NSWindow):
    # Explicitly allow key window status
    def canBecomeKeyWindow(self):
        return True

    # Required to capture "Command+..." sequences.
    def keyDown_(self, event):
        delegate = self.delegate()
        if delegate is not None:
            delegate.keyDown_(event)


# Custom view (contains click-and-drag area on top sliver of overlay).
class DragArea(NSView):
    def initWithFrame_(self, frame):
        objc.super(DragArea, self).initWithFrame_(frame)
        self.setWantsLayer_(True)
        return self

    # Used to update top-bar background to (roughly) match app color.
    def setBackgroundColor_(self, color):
        self.layer().setBackgroundColor_(color.CGColor())

    # Used to capture the click-and-drag event.
    def mouseDown_(self, event):
        self.window().performWindowDragWithEvent_(event)


# The main delegate for running the overlay app.
class AppDelegate(NSObject):
    # The main application setup.
    def applicationDidFinishLaunching_(self, notification):
        # Run as accessory app
        NSApp.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
        # Create a borderless, floating, resizable window
        self.window = AppWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(500, 200, 970, 750),
            NSBorderlessWindowMask | NSResizableWindowMask,
            NSBackingStoreBuffered,
            False
        )
        self.window.setLevel_(NSFloatingWindowLevel)
        self.window.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorStationary
        )
        # Save the last position and size
        self.window.setFrameAutosaveName_(FRAME_SAVE_NAME)
        # Create the webview for the main application.
        config = WKWebViewConfiguration.alloc().init()
        config.preferences().setJavaScriptCanOpenWindowsAutomatically_(True)
        # Initialize the WebView with a frame
        self.webview = WKWebView.alloc().initWithFrame_configuration_(
            ((0, 0), (970, 750)),  # Frame: origin (0,0), size (970x750)
            config
        )
        self.webview.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)  # Resizes with window
        # Set a custom user agent
        safari_user_agent = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"
        self.webview.setCustomUserAgent_(safari_user_agent)
        # Make window transparent so that the corners can be rounded
        self.window.setOpaque_(False)
        self.window.setBackgroundColor_(NSColor.clearColor())
        # Set up content view with rounded corners
        content_view = NSView.alloc().initWithFrame_(self.window.contentView().bounds())
        content_view.setWantsLayer_(True)
        content_view.layer().setCornerRadius_(CORNER_RADIUS)
        content_view.layer().setBackgroundColor_(NSColor.whiteColor().CGColor())
        self.window.setContentView_(content_view)
        # Set up drag area (top sliver, full width)
        content_bounds = content_view.bounds()
        self.drag_area = DragArea.alloc().initWithFrame_(
            NSMakeRect(0, content_bounds.size.height - DRAG_AREA_HEIGHT, content_bounds.size.width, DRAG_AREA_HEIGHT)
        )
        content_view.addSubview_(self.drag_area)
        # Add close button to the drag area
        close_button = NSButton.alloc().initWithFrame_(NSMakeRect(5, 5, 20, 20))
        close_button.setBordered_(False)
        close_button.setImage_(NSImage.imageWithSystemSymbolName_accessibilityDescription_("xmark.circle.fill", None))
        close_button.setTarget_(self)
        close_button.setAction_("hideWindow:")
        self.drag_area.addSubview_(close_button)
        # Update the webview sizing and insert it below drag area.
        content_view.addSubview_(self.webview)
        self.webview.setFrame_(NSMakeRect(0, 0, content_bounds.size.width, content_bounds.size.height - DRAG_AREA_HEIGHT))
        # Contact the target website.
        url = NSURL.URLWithString_(WEBSITE)
        request = NSURLRequest.requestWithURL_(url)
        self.webview.loadRequest_(request)
        # Set self as navigation delegate to know when page loads
        self.webview.setNavigationDelegate_(self)
        # Set up script message handlers for background color changes and debug logging
        configuration = self.webview.configuration()
        user_content_controller = configuration.userContentController()
        user_content_controller.addScriptMessageHandler_name_(self, "backgroundColorHandler")
        user_content_controller.addScriptMessageHandler_name_(self, "debugLogger")
        # Inject JavaScript to monitor background color changes and debug focus/DOM issues
        script = """
            function sendBackgroundColor() {
                var bgColor = window.getComputedStyle(document.body).backgroundColor;
                window.webkit.messageHandlers.backgroundColorHandler.postMessage(bgColor);
            }
            window.addEventListener('load', sendBackgroundColor);
            new MutationObserver(sendBackgroundColor).observe(document.body, { attributes: true, attributeFilter: ['style'] });

            // DEBUG: Send logs to Python instead of console
            function debugLog(message) {
                try {
                    window.webkit.messageHandlers.debugLogger.postMessage(message);
                } catch(e) {
                    console.log('[DEBUG]', message);
                }
            }

            // DEBUG: Comprehensive logging for Enter key issue
            let enterPressCount = 0;
            let lastFocusedElement = null;
            let textareaInstances = new Set();

            function logDetailedState(context) {
                const textareas = document.querySelectorAll('textarea');
                const promptSelectors = '[aria-label="Enter a prompt here"], [data-placeholder="Ask Gemini"]';
                const promptElement = document.querySelector(promptSelectors);
                const activeEl = document.activeElement;

                // Look for submit buttons and form elements
                const submitButtons = document.querySelectorAll('button[type="submit"], button[aria-label*="Send"], button[data-testid*="send"], [role="button"][aria-label*="Send"]');
                const sendButtons = document.querySelectorAll('button:not([disabled])').length;

                const message = `${context} - Textareas: ${textareas.length}, Prompt exists: ${!!promptElement}, Active: ${activeEl?.tagName}${activeEl?.getAttribute ? ` (${activeEl.getAttribute('aria-label') || activeEl.getAttribute('data-placeholder') || 'no-label'})` : ''}, Focused textarea: ${activeEl?.tagName === 'TEXTAREA'}, Submit buttons: ${submitButtons.length}, Total buttons: ${sendButtons}`;
                debugLog(message);

                // Log each textarea's properties
                textareas.forEach((ta, i) => {
                    debugLog(`  Textarea ${i}: disabled=${ta.disabled}, readonly=${ta.readOnly}, value.length=${ta.value.length}, focused=${ta === activeEl}`);
                });

                // Log prompt element details
                if (promptElement) {
                    debugLog(`  Prompt element: ${promptElement.tagName}, contentEditable=${promptElement.contentEditable}, textContent.length=${promptElement.textContent?.length || 0}, innerHTML.length=${promptElement.innerHTML?.length || 0}`);
                }

                // Log submit buttons
                submitButtons.forEach((btn, i) => {
                    debugLog(`  Submit button ${i}: ${btn.tagName}, disabled=${btn.disabled}, aria-label="${btn.getAttribute('aria-label') || 'none'}", visible=${btn.offsetParent !== null}`);
                });
            }

            // Track textarea instances
            function trackTextarea(textarea, action) {
                const id = textarea.getAttribute('data-debug-id') || Math.random().toString(36);
                textarea.setAttribute('data-debug-id', id);
                debugLog(`Textarea ${action}: ${id}`);
                if (action === 'added') textareaInstances.add(id);
                if (action === 'removed') textareaInstances.delete(id);
            }

            // Monitor ALL keyboard events on prompt elements (DIV or TEXTAREA)
            document.addEventListener('keydown', function(e) {
                const isPromptElement = e.target.tagName === 'TEXTAREA' ||
                                      (e.target.tagName === 'DIV' && e.target.contentEditable === 'true') ||
                                      e.target.matches('[aria-label*="Enter a prompt"], [data-placeholder*="Ask Gemini"]');

                if (isPromptElement && e.key === 'Enter') {
                    enterPressCount++;
                    debugLog(`ENTER KEYDOWN #${enterPressCount} - Target: ${e.target.tagName}, ContentEditable: ${e.target.contentEditable}, Focused: ${e.target === document.activeElement}, Default prevented: ${e.defaultPrevented}, Shift: ${e.shiftKey}, Ctrl: ${e.ctrlKey}`);

                    // Check if there's text content to submit
                    const textContent = e.target.textContent || e.target.value || '';
                    debugLog(`  Text content: "${textContent}" (length: ${textContent.length})`);

                    logDetailedState(`Enter keydown #${enterPressCount}`);
                }
            }, true);

            document.addEventListener('keypress', function(e) {
                const isPromptElement = e.target.tagName === 'TEXTAREA' ||
                                      (e.target.tagName === 'DIV' && e.target.contentEditable === 'true') ||
                                      e.target.matches('[aria-label*="Enter a prompt"], [data-placeholder*="Ask Gemini"]');

                if (isPromptElement && e.key === 'Enter') {
                    debugLog(`ENTER KEYPRESS #${enterPressCount} - Default prevented: ${e.defaultPrevented}`);
                }
            }, true);

            document.addEventListener('keyup', function(e) {
                const isPromptElement = e.target.tagName === 'TEXTAREA' ||
                                      (e.target.tagName === 'DIV' && e.target.contentEditable === 'true') ||
                                      e.target.matches('[aria-label*="Enter a prompt"], [data-placeholder*="Ask Gemini"]');

                if (isPromptElement && e.key === 'Enter') {
                    debugLog(`ENTER KEYUP #${enterPressCount} - Default prevented: ${e.defaultPrevented}`);
                    // Log state after keyup
                    setTimeout(() => logDetailedState(`After Enter keyup #${enterPressCount}`), 10);
                }
            }, true);

            // Monitor focus changes on prompt elements
            document.addEventListener('focusin', function(e) {
                const isPromptElement = e.target.tagName === 'TEXTAREA' ||
                                      (e.target.tagName === 'DIV' && e.target.contentEditable === 'true') ||
                                      e.target.matches('[aria-label*="Enter a prompt"], [data-placeholder*="Ask Gemini"]');

                if (isPromptElement) {
                    debugLog(`Prompt element FOCUS IN: ${e.target.tagName} (${e.target.getAttribute('data-debug-id') || 'new'})`);
                    lastFocusedElement = e.target;
                    logDetailedState('Focus in');
                }
            });

            document.addEventListener('focusout', function(e) {
                const isPromptElement = e.target.tagName === 'TEXTAREA' ||
                                      (e.target.tagName === 'DIV' && e.target.contentEditable === 'true') ||
                                      e.target.matches('[aria-label*="Enter a prompt"], [data-placeholder*="Ask Gemini"]');

                if (isPromptElement) {
                    debugLog(`Prompt element FOCUS OUT: ${e.target.tagName} (${e.target.getAttribute('data-debug-id') || 'unknown'})`);
                    logDetailedState('Focus out');
                }
            });

            // Monitor DOM changes
            const observer = new MutationObserver(function(mutations) {
                mutations.forEach(function(mutation) {
                    if (mutation.type === 'childList') {
                        mutation.addedNodes.forEach(function(node) {
                            if (node.nodeType === 1) {
                                if (node.tagName === 'TEXTAREA') {
                                    trackTextarea(node, 'added');
                                    logDetailedState('Textarea added');
                                } else if (node.querySelector && node.querySelector('textarea')) {
                                    node.querySelectorAll('textarea').forEach(ta => {
                                        trackTextarea(ta, 'added');
                                    });
                                    logDetailedState('Container with textarea added');
                                }
                            }
                        });
                        mutation.removedNodes.forEach(function(node) {
                            if (node.nodeType === 1) {
                                if (node.tagName === 'TEXTAREA') {
                                    trackTextarea(node, 'removed');
                                    logDetailedState('Textarea removed');
                                } else if (node.querySelector && node.querySelector('textarea')) {
                                    node.querySelectorAll('textarea').forEach(ta => {
                                        trackTextarea(ta, 'removed');
                                    });
                                    logDetailedState('Container with textarea removed');
                                }
                            }
                        });
                    }
                });
            });
            observer.observe(document.body, { childList: true, subtree: true });

            // Monitor form submissions and button clicks
            document.addEventListener('submit', function(e) {
                debugLog(`FORM SUBMIT detected - target: ${e.target.tagName}, action: ${e.target.action || 'none'}`);
            }, true);

            document.addEventListener('click', function(e) {
                if (e.target.tagName === 'BUTTON' || e.target.role === 'button') {
                    debugLog(`BUTTON CLICK - target: ${e.target.tagName}, aria-label: "${e.target.getAttribute('aria-label') || 'none'}", disabled: ${e.target.disabled}`);
                }
            }, true);

            // Monitor for any programmatic form submissions or AJAX requests
            const originalFetch = window.fetch;
            window.fetch = function(...args) {
                debugLog(`FETCH REQUEST - URL: ${args[0]}`);
                return originalFetch.apply(this, args);
            };

            const originalXHROpen = XMLHttpRequest.prototype.open;
            XMLHttpRequest.prototype.open = function(method, url) {
                debugLog(`XHR REQUEST - ${method} ${url}`);
                return originalXHROpen.apply(this, arguments);
            };

            // Log initial state
            setTimeout(() => logDetailedState('Initial state'), 1000);
        """
        user_script = WKUserScript.alloc().initWithSource_injectionTime_forMainFrameOnly_(script, WKUserScriptInjectionTimeAtDocumentEnd, True)
        user_content_controller.addUserScript_(user_script)
        # Create status bar item with logo
        self.status_item = NSStatusBar.systemStatusBar().statusItemWithLength_(NSSquareStatusItemLength)
        script_dir = os.path.dirname(os.path.abspath(__file__))
        logo_white_path = os.path.join(script_dir, LOGO_WHITE_PATH)
        self.logo_white = NSImage.alloc().initWithContentsOfFile_(logo_white_path)
        self.logo_white.setSize_(NSSize(18, 18))
        logo_black_path = os.path.join(script_dir, LOGO_BLACK_PATH)
        self.logo_black = NSImage.alloc().initWithContentsOfFile_(logo_black_path)
        self.logo_black.setSize_(NSSize(18, 18))
        # Set the initial logo image based on the current appearance
        self.updateStatusItemImage()
        # Observe system appearance changes
        self.status_item.button().addObserver_forKeyPath_options_context_(
            self, "effectiveAppearance", NSKeyValueObservingOptionNew, STATUS_ITEM_CONTEXT
        )
        # Create status bar menu
        menu = NSMenu.alloc().init()
        # Create and configure menu items with explicit targets
        show_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Show "+APP_TITLE, "showWindow:", "")
        show_item.setTarget_(self)
        menu.addItem_(show_item)
        hide_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Hide "+APP_TITLE, "hideWindow:", "h")
        hide_item.setTarget_(self)
        menu.addItem_(hide_item)
        home_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Home", "goToWebsite:", "g")
        home_item.setTarget_(self)
        menu.addItem_(home_item)
        clear_data_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Clear Web Cache", "clearWebViewData:", "")
        clear_data_item.setTarget_(self)
        menu.addItem_(clear_data_item)
        set_trigger_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Set New Trigger", "setTrigger:", "")
        set_trigger_item.setTarget_(self)
        menu.addItem_(set_trigger_item)
        install_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Install Autolauncher", "install:", "")
        install_item.setTarget_(self)
        menu.addItem_(install_item)
        uninstall_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Uninstall Autolauncher", "uninstall:", "")
        uninstall_item.setTarget_(self)
        menu.addItem_(uninstall_item)
        quit_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Quit", "terminate:", "q")
        quit_item.setTarget_(NSApp)
        menu.addItem_(quit_item)
        # Set the menu for the status item
        self.status_item.setMenu_(menu)
        # Add resize observer
        NSNotificationCenter.defaultCenter().addObserver_selector_name_object_(
            self, 'windowDidResize:', NSWindowDidResizeNotification, self.window
        )
        # Add local mouse event monitor for left mouse down
        self.local_mouse_monitor = NSEvent.addLocalMonitorForEventsMatchingMask_handler_(
            NSEventMaskLeftMouseDown,  # Monitor left mouse-down events
            self.handleLocalMouseEvent  # Handler method
        )
        # Load the custom launch trigger if the user set it.
        load_custom_launcher_trigger()
        # Set the delegate of the window to this parent application BEFORE starting event processing.
        self.window.setDelegate_(self)
        # Create the event tap for key-down events
        self.event_tap = CGEventTapCreate(
            kCGSessionEventTap, # Tap at the session level
            kCGHeadInsertEventTap, # Insert at the head of the event queue
            kCGEventTapOptionDefault, # Actively filter events
            CGEventMaskBit(kCGEventKeyDown), # Capture key-down events
            global_show_hide_listener(self), # Your callback function
            None # Optional user info (refcon)
        )
        if self.event_tap:
            # Integrate the tap into NSApplication's main run loop (not a separate CFRunLoop)
            self.event_tap_source = CFMachPortCreateRunLoopSource(None, self.event_tap, 0)
            CFRunLoopAddSource(CFRunLoopGetMain(), self.event_tap_source, kCFRunLoopCommonModes)
            CGEventTapEnable(self.event_tap, True)

            # Set up a timer to monitor and re-enable the event tap if it gets disabled
            self.tap_monitor_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                1.0, self, 'monitorEventTap:', None, True
            )

            print("Event tap successfully integrated into main run loop", flush=True)
        else:
            print("Failed to create event tap. Check Accessibility permissions.")
        # Make sure this window is shown and focused.
        self.showWindow_(None)

    # Logic to show the overlay, make it the key window, and focus on the typing area.
    def showWindow_(self, sender):
        print(f"[DEBUG] showWindow_ called (sender: {sender})", flush=True)
        self.window.makeKeyAndOrderFront_(None)
        NSApp.activateIgnoringOtherApps_(True)
        self._focus_prompt_area()

    # Hide the overlay and allow focus to return to the next visible application.
    def hideWindow_(self, sender):
        NSApp.hide_(None)

    # Go to the default landing website for the overlay (in case accidentally navigated away).
    def goToWebsite_(self, sender):
        url = NSURL.URLWithString_(WEBSITE)
        request = NSURLRequest.requestWithURL_(url)
        self.webview.loadRequest_(request)

    # Clear the webview cache data (in case cookies cause errors).
    def clearWebViewData_(self, sender):
        dataStore = self.webview.configuration().websiteDataStore()
        dataTypes = WKWebsiteDataStore.allWebsiteDataTypes()
        dataStore.removeDataOfTypes_modifiedSince_completionHandler_(
            dataTypes,
            NSDate.distantPast(),
            lambda: print("Data cleared")
        )

    # Go to the default landing website for the overlay (in case accidentally navigated away).
    def install_(self, sender):
        if install_startup():
            # Exit the current process since a new one will launch.
            print("Installation successful, exiting.", flush=True)
            NSApp.terminate_(None)
        else:
            print("Installation unsuccessful.", flush=True)

    # Go to the default landing website for the overlay (in case accidentally navigated away).
    def uninstall_(self, sender):
        if uninstall_startup():
            NSApp.hide_(None)

    # Handle the 'Set Trigger' menu item click.
    def setTrigger_(self, sender):
        set_custom_launcher_trigger(self)

    # For capturing key commands while the key window (in focus).
    def keyDown_(self, event):
        modifiers = event.modifierFlags()
        key_command = modifiers & NSCommandKeyMask
        key_alt = modifiers & NSAlternateKeyMask
        key_shift = modifiers & NSShiftKeyMask
        key_control = modifiers & NSControlKeyMask
        key = event.charactersIgnoringModifiers()
        # Command (NOT alt)
        if (key_command or key_control) and (not key_alt):
            # Select all
            if key == 'a':
                self.window.firstResponder().selectAll_(None)
            # Copy
            elif key == 'c':
                self.window.firstResponder().copy_(None)
            # Cut
            elif key == 'x':
                self.window.firstResponder().cut_(None)
            # Paste
            elif key == 'v':
                self.window.firstResponder().paste_(None)
            # Hide
            elif key == 'h':
                self.hideWindow_(None)
            # New Chat (Command+N)
            elif key == 'n':
                # Try to click Gemini's "New chat" button (falls back to reload)
                js = """
                (function(){
                  const sel = '[aria-label="New chat"], [aria-label="New conversation"], [data-command="new-conversation"]';
                  const btn = document.querySelector(sel);
                  if(btn){ btn.click(); } else { location.href='https://gemini.google.com/?referrer=macos-gemini-overlay'; }
                })();
                """
                self.webview.evaluateJavaScript_completionHandler_(js, None)
            # Toggle Sidebar (Ctrl+Cmd+S)
            elif key == 's' and key_control and key_command:
                js = """
                (function(){
                  const selectors=[
                    '[aria-label="Main menu"]',
                    '[data-test-id="side-nav-menu-button"]'
                  ];
                  let btn=null;
                  for(const sel of selectors){ btn=document.querySelector(sel); if(btn) break; }
                  if(btn){ btn.click(); }
                })();
                """
                self.webview.evaluateJavaScript_completionHandler_(js, None)
            # Quit
            elif key == 'q':
                NSApp.terminate_(None)
            # Open Saved Info (Cmd + ,)
            elif key == ',' and key_command and not key_control and not key_alt:
                js = """
                (function(){
                  function clickSettings(){
                    const btn=document.querySelector('[aria-label="Settings & help"], [data-test-id="settings-and-help-button"]');
                    if(btn){ btn.click(); return true; }
                    return false;
                  }
                  function clickSaved(){
                    let link=document.querySelector('a[href*="/saved-info"]');
                    if(!link){
                      // fallback: find menu item whose text includes "Saved info"
                      const items=document.querySelectorAll('a[role="menuitem"], button[role="menuitem"]');
                      for(const el of items){
                        if(el.textContent && el.textContent.trim().toLowerCase().includes('saved info')){ link=el; break; }
                      }
                    }
                    if(link){ link.click(); }
                  }
                  if(clickSettings()){
                    setTimeout(clickSaved, 50);
                  }
                })();
                """
                self.webview.evaluateJavaScript_completionHandler_(js, None)
            # # Undo (causes crash for some reason)
            # elif key == 'z':
            #     self.window.firstResponder().undo_(None)

    # Handler for capturing a click-and-drag event when not already the key window.
    @objc.python_method
    def handleLocalMouseEvent(self, event):
        if event.window() == self.window:
            # Get the click location in window coordinates
            click_location = event.locationInWindow()
            # Use hitTest_ to determine which view receives the click
            hit_view = self.window.contentView().hitTest_(click_location)
            # Check if the hit view is the drag area
            if hit_view == self.drag_area:
                # Bring the window to the front and make it key
                self.showWindow_(None)
                # Initiate window dragging with the event
                self.window.performWindowDragWithEvent_(event)
                return None  # Consume the event
        return event  # Pass unhandled events along

    # Handler for when the window resizes (adjusts the drag area).
    def windowDidResize_(self, notification):
        bounds = self.window.contentView().bounds()
        w, h = bounds.size.width, bounds.size.height
        self.drag_area.setFrame_(NSMakeRect(0, h - DRAG_AREA_HEIGHT, w, DRAG_AREA_HEIGHT))
        self.webview.setFrame_(NSMakeRect(0, 0, w, h - DRAG_AREA_HEIGHT))

    # Handler for setting the background color based on the web page background color.
    def userContentController_didReceiveScriptMessage_(self, userContentController, message):
        if message.name() == "backgroundColorHandler":
            bg_color_str = message.body()
            # Convert CSS color to NSColor (assuming RGB for simplicity)
            if bg_color_str.startswith("rgb") and ("(" in bg_color_str) and (")" in bg_color_str):
                rgb_values = [float(val) for val in bg_color_str[bg_color_str.index("(")+1:bg_color_str.index(")")].split(",")]
                r, g, b = [val / 255.0 for val in rgb_values[:3]]
                color = NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, 1.0)
                self.drag_area.setBackgroundColor_(color)
        elif message.name() == "debugLogger":
            debug_message = message.body()
            print(f"[JS DEBUG] {debug_message}", flush=True)

    # Logic for checking what color the logo in the status bar should be, and setting appropriate logo.
    def updateStatusItemImage(self):
        appearance = self.status_item.button().effectiveAppearance()
        if appearance.bestMatchFromAppearancesWithNames_([NSAppearanceNameAqua, NSAppearanceNameDarkAqua]) == NSAppearanceNameDarkAqua:
            self.status_item.button().setImage_(self.logo_white)
        else:
            self.status_item.button().setImage_(self.logo_black)

    # Observer that is triggered whenever the color of the status bar logo might need to be updated.
    def observeValueForKeyPath_ofObject_change_context_(self, keyPath, object, change, context):
        if context == STATUS_ITEM_CONTEXT and keyPath == "effectiveAppearance":
            self.updateStatusItemImage()

    # System triggered appearance changes that might affect logo color.
    def appearanceDidChange_(self, notification):
        # Update the logo image when the system appearance changes
        self.updateStatusItemImage()

    # WKNavigationDelegate – called when navigation finishes
    def webView_didFinishNavigation_(self, webview, navigation):
        print(f"[DEBUG] Navigation finished, scheduling focus timer", flush=True)
        # Page loaded, focus prompt area after small delay to ensure textarea exists
        # Delay 0.1 s, then focus prompt (use NSTimer – PyObjC provides selector call)
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            0.1, self, '_focusPromptTimerFired:', None, False)

    # Helper called by timer
    def _focusPromptTimerFired_(self, timer):
        print(f"[DEBUG] Focus timer fired after navigation", flush=True)
        self._focus_prompt_area()

    # Python method to call JS that focuses the Gemini textarea / prompt
    @objc.python_method
    def _focus_prompt_area(self):
        import time
        timestamp = time.time()
        print(f"[DEBUG] _focus_prompt_area called at {timestamp}", flush=True)
        js_focus = f"""
        (function(){{
          debugLog('Python focus attempt at {timestamp}');
          const sel='[aria-label=\\"Enter a prompt here\\"], [data-placeholder=\\"Ask Gemini\\"]';
          const el=document.querySelector(sel) || document.querySelector('textarea');
          const currentFocus = document.activeElement;
          debugLog('Focus attempt - selector found: ' + !!document.querySelector(sel) + ', textarea found: ' + !!document.querySelector('textarea') + ', element to focus: ' + (el?.tagName || 'none') + ', currently focused: ' + (currentFocus?.tagName || 'none'));
          if(el){{
            debugLog('About to call focus() on element');
            el.focus();
            setTimeout(() => {{
              debugLog('Focus result - now focused: ' + (document.activeElement === el) + ', active element: ' + (document.activeElement?.tagName || 'none'));
            }}, 5);
          }} else {{
            debugLog('No element found to focus');
          }}
        }})();
        """
        self.webview.evaluateJavaScript_completionHandler_(js_focus, None)

    # Monitor the event tap and re-enable it if it gets disabled
    def monitorEventTap_(self, timer):
        if hasattr(self, 'event_tap') and self.event_tap:
            if not CGEventTapIsEnabled(self.event_tap):
                print("Event tap was disabled, re-enabling...", flush=True)
                CGEventTapEnable(self.event_tap, True)
