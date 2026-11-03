<?php
/**
 * Search results.
 *
 * @package wp-bootstrap-starter
 */

defined( 'ABSPATH' ) || exit;

get_header();
?>
<header class="search-header mb-4">
	<h1 class="h3">
		<?php
		printf(
			/* translators: %s: search query. */
			esc_html__( 'Search results for: %s', WBS_TEXTDOMAIN ),
			'<span class="text-primary">' . esc_html( get_search_query() ) . '</span>'
		);
		?>
	</h1>
	<?php get_search_form(); ?>
</header>

<?php
if ( have_posts() ) :
	while ( have_posts() ) :
		the_post();
		get_template_part( 'template-parts/content', get_post_type() );
	endwhile;
	wbs_pagination();
else :
	get_template_part( 'template-parts/content', 'none' );
endif;

get_footer();
