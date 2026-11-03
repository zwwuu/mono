<?php
/**
 * WP Bootstrap Starter — theme bootstrap file.
 *
 * No build step, no dependencies: plain PHP + vendored Bootstrap 5.
 * Everything is organized under /inc so functions.php stays a manifest.
 *
 * @package wp-bootstrap-starter
 */

defined( 'ABSPATH' ) || exit;

require_once get_template_directory() . '/inc/config.php';       // Constants & toggles.
require_once get_template_directory() . '/inc/setup.php';        // Theme support, menus, image sizes.
require_once get_template_directory() . '/inc/enqueue.php';      // Local CSS/JS only — no CDN, no webfonts.
require_once get_template_directory() . '/inc/nav-walker.php';   // Bootstrap 5 dropdown menu walker.
require_once get_template_directory() . '/inc/template-tags.php'; // Meta, pagination, comments, menu fallback.
require_once get_template_directory() . '/inc/customizer.php';   // Theme options (layout, navbar, footer text).
//require_once get_template_directory() . '/inc/intranet.php';     // Intranet mode: block all internet access.
