<?php
/**
 * Asset loading — 100% local. No CDN, no Google Fonts, no external requests.
 *
 * Bootstrap's default font stack is system fonts, so nothing remote
 * is needed for typography either.
 *
 * @package wp-bootstrap-starter
 */

defined( 'ABSPATH' ) || exit;

add_action( 'wp_enqueue_scripts', 'wbs_enqueue_assets' );
/**
 * Enqueue vendored Bootstrap + theme stylesheet/script.
 */
function wbs_enqueue_assets() {
	wp_enqueue_style(
		'wbs-bootstrap',
		WBS_ASSETS_URI . '/css/bootstrap.min.css',
		array(),
		WBS_BOOTSTRAP_VERSION
	);

	wp_enqueue_style(
		'wbs-style',
		get_stylesheet_uri(),
		array( 'wbs-bootstrap' ),
		WBS_VERSION
	);

	wp_enqueue_script(
		'wbs-bootstrap',
		WBS_ASSETS_URI . '/js/bootstrap.bundle.min.js',
		array(),
		WBS_BOOTSTRAP_VERSION,
		true
	);

	if ( is_singular() && comments_open() && get_option( 'thread_comments' ) ) {
		wp_enqueue_script( 'comment-reply' );
	}
}
