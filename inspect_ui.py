"""Kalibrační nástroj: vypíše prvky aktuální obrazovky appky (text, popisky, id, třída).
Slouží k úpravě selectors.json (Android) nebo selectors.mac.json (Mac).

Postup: ručně otevři v Liftagu obrazovku, kterou chceš zkoumat, a spusť:
  python inspect_ui.py
Pro Mac musí běžet Appium (appium) a appka musí být otevřená.
"""
import re

from appium import webdriver

import config

if config.PLATFORM == "mac":
    from appium.options.common import AppiumOptions

    opts = AppiumOptions()
    opts.set_capability("platformName", "mac")
    opts.set_capability("appium:automationName", "mac2")
    # Bez bundleId se Appium k appce jen připojí (nerestartuje ji ani nevrací na úvod).
else:
    from appium.options.android import UiAutomator2Options

    opts = UiAutomator2Options()
    opts.platform_name = "Android"
    opts.no_reset = True
    opts.dont_stop_app_on_reset = True
    opts.app_package = config.LIFTAGO_PACKAGE
    if config.ANDROID_UDID:
        opts.udid = config.ANDROID_UDID

d = webdriver.Remote(config.APPIUM_URL, options=opts)
try:
    if config.PLATFORM == "mac":
        import time

        # jen přenese už otevřenou appku do popředí, bez restartu
        d.execute_script("macos: activateApp", {"bundleId": config.LIFTAGO_BUNDLE_ID})
        time.sleep(1.5)
    src = d.page_source
    for cls, attrs in re.findall(r"<([\w.$]+)([^>]*)>", src):
        a = dict(re.findall(r'([\w-]+)="([^"]*)"', attrs))
        txt = a.get("text") or a.get("label") or a.get("title") or a.get("value") or ""
        desc = a.get("content-desc", "")
        ident = a.get("resource-id") or a.get("identifier", "")
        if cls.endswith(("MenuBarItem", "MenuItem", "MenuBar", "Menu")):
            continue  # menu systému s posledními soubory nás nezajímá (a je soukromé)
        if txt or desc or ident:
            print(f"{cls.split('.')[-1]:26} text={txt!r:32} desc={desc!r:22} id={ident}")
finally:
    d.quit()
