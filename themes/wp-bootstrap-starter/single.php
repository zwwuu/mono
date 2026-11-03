<?php
/**
 * Single post view.
 *
 * Extensibility hooks: 'wbs_entry_top', 'wbs_entry_bottom'.
 *
 * @package wp-bootstrap-starter
 */

defined( 'ABSPATH' ) || exit;

get_header();

while ( have_posts() ) :
	the_post();
	?>
	<article id="post-<?php the_ID(); ?>" <?php post_class(); ?>>
		<?php do_action( 'wbs_entry_top' ); ?>

		<header class="entry-header mb-3">
			<?php the_title( '<h1 class="entry-title">', '</h1>' ); ?>
			<div class="entry-meta text-muted small"><?php wbs_posted_on(); ?></div>
		</header>

		<?php if ( has_post_thumbnail() ) : ?>
			<figure class="entry-thumbnail mb-4">
				<?php the_post_thumbnail( 'wbs-featured', array( 'class' => 'img-fluid rounded' ) ); ?>
			</figure>
		<?php endif; ?>

		<div class="entry-content">
			<?php the_content(); ?>
		</div>

		<footer class="entry-footer mt-4 pt-3 border-top small text-muted">
			<?php wbs_posted_in(); ?>
		</footer>

		<?php do_action( 'wbs_entry_bottom' ); ?>
	</article>

	<?php
	the_post_navigation(
		array(
			'prev_text' => '<span aria-hidden="true">&larr;</span> %title',
			'next_text' => '%title <span aria-hidden="true">&rarr;</span>',
		)
	);

	if ( comments_open() || get_comments_number() ) {
		comments_template();
	}
	?>
	<?php
endwhile;

get_footer();
