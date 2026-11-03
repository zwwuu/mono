<?php
/**
 * Main template: blog index and default fallback.
 *
 * Extensibility hooks: 'wbs_before_loop', 'wbs_after_loop'.
 *
 * @package wp-bootstrap-starter
 */

defined( 'ABSPATH' ) || exit;

get_header();

do_action( 'wbs_before_loop' );

if ( have_posts() ) :
	while ( have_posts() ) :
		the_post();
		get_template_part( 'template-parts/content', get_post_type() );
	endwhile;
	wbs_pagination();
else :
	get_template_part( 'template-parts/content', 'none' );
endif;

do_action( 'wbs_after_loop' );

get_footer();
