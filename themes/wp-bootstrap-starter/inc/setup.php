<?php
/**
 * Theme setup: supports, menus, image sizes. (No widget areas — the theme is widget-free.)
 *
 * @package wp-bootstrap-starter
 */

defined( 'ABSPATH' ) || exit;

add_action( 'after_setup_theme', 'wbs_theme_setup' );
/**
 * Register the must-have theme features.
 */
function wbs_theme_setup() {
	load_theme_textdomain( WBS_TEXTDOMAIN, WBS_DIR . '/languages' );

	add_theme_support( 'title-tag' );
	add_theme_support( 'post-thumbnails' );
	add_theme_support( 'automatic-feed-links' );
	add_theme_support( 'customize-selective-refresh-widgets' );
	add_theme_support( 'responsive-embeds' );
	add_theme_support(
		'html5',
		array( 'comment-list', 'comment-form', 'search-form', 'gallery', 'caption', 'style', 'script' )
	);
	add_theme_support(
		'custom-logo',
		array(
			'height'      => 60,
			'width'       => 200,
			'flex-height' => true,
			'flex-width'  => true,
		)
	);

	register_nav_menus(
		array(
			'primary' => __( 'Primary menu', WBS_TEXTDOMAIN ),
			'footer'  => __( 'Footer menu', WBS_TEXTDOMAIN ),
		)
	);

	// Featured image size used by the card layout.
	add_image_size( 'wbs-featured', 1200, 630, true );
}

